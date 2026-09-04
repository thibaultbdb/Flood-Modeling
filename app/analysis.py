"""
Flood risk analysis engine.

Replicates the flood (FL) branch of GFDRR CCDR-tools `runAnalysis.py`:

1. Load exposure raster (population / built-up / agriculture), clean nodata.
2. Compute total exposure per admin unit (zonal sum, all_touched=True).
3. For each return period, warp the Fathom hazard raster onto the exposure
   grid (WarpedVRT, nearest resampling), apply the minimum hazard threshold,
   then either:
     - "Function": apply the depth-impact function and compute exposed +
       impacted totals per admin unit, or
     - "Classes":  bin depths into user classes and compute cumulative
       exposure per class per admin unit.
4. Combine return periods into Expected Annual Impact / Exposure (EAI / EAE)
   using the exceedance-frequency weights (lower-bound, upper-bound, mean),
   exactly as CCDR-tools does. The "Mean" estimate is reported as the main
   result, with LB/UB kept as columns.

Hazard depths are handled in centimetres (Fathom v3 native unit).
"""
import gc
import os
import re

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
import rasterio.vrt
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from affine import Affine

from damage_functions import get_damage_function


RP_FILENAME_PATTERNS = [
    re.compile(r"1in(\d+)", re.IGNORECASE),
    re.compile(r"rp[_\-]?(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)[_\-]?(?:yr|year)", re.IGNORECASE),
]


def detect_return_period(filename):
    """Guess the return period from a Fathom-style filename (e.g. 1in100)."""
    base = os.path.basename(filename)
    for pat in RP_FILENAME_PATTERNS:
        m = pat.search(base)
        if m:
            return int(m.group(1))
    return None


def _feature_window(geom_bounds, transform, shape):
    """Integer pixel window covering a geometry's bounds, clipped to raster."""
    win = from_bounds(*geom_bounds, transform=transform)
    row0 = max(0, int(np.floor(win.row_off)))
    col0 = max(0, int(np.floor(win.col_off)))
    row1 = min(shape[0], int(np.ceil(win.row_off + win.height)))
    col1 = min(shape[1], int(np.ceil(win.col_off + win.width)))
    if row1 <= row0 or col1 <= col0:
        return None
    return row0, row1, col0, col1


def zonal_sum(array, geoms, transform, all_touched=False):
    """
    Sum of `array` inside each geometry; NaN cells are ignored.

    `all_touched` mirrors rasterstats: True counts every pixel the polygon
    touches, False only pixels whose centre falls inside it. CCDR-tools uses
    True for the admin-unit exposure totals and rasterstats' default (False)
    for the per-return-period statistics, so we do the same by default.
    """
    shape = array.shape
    out = np.zeros(len(geoms), dtype='float64')
    for i, geom in enumerate(geoms):
        if geom is None or geom.is_empty:
            continue
        w = _feature_window(geom.bounds, transform, shape)
        if w is None:
            continue
        row0, row1, col0, col1 = w
        sub = array[row0:row1, col0:col1]
        sub_transform = transform * Affine.translation(col0, row0)
        mask = geometry_mask([geom.__geo_interface__], out_shape=sub.shape,
                             transform=sub_transform, invert=True, all_touched=all_touched)
        if mask.any():
            out[i] = np.nansum(sub[mask])
    return out


def exceedance_freq_table(valid_RPs):
    """Exceedance-frequency weights per RP (LB / UB / Mean), as CCDR-tools."""
    valid_RPs = sorted(valid_RPs)
    prob_RPs = 1.0 / np.array(valid_RPs, dtype='float64')
    prob_RPs_LB = np.append(-np.diff(prob_RPs), prob_RPs[-1])
    prob_RPs_UB = np.insert(-np.diff(prob_RPs), 0, 0.0)
    prob_RPs_Mean = (prob_RPs_LB + prob_RPs_UB) / 2
    return pd.DataFrame({
        'RPs': valid_RPs,
        'prob_RPs': prob_RPs,
        'prob_RPs_LB': prob_RPs_LB,
        'prob_RPs_UB': prob_RPs_UB,
        'prob_RPs_Mean': prob_RPs_Mean,
    })


def load_exposure(exp_path, bounds=None):
    """
    Read the exposure raster as float32. Nodata -> NaN, negatives -> 0
    (mirrors CCDR-tools' handling). Optionally crop to `bounds`
    (minx, miny, maxx, maxy in the raster CRS) to bound memory use.
    """
    with rasterio.open(exp_path) as src:
        transform = src.transform
        window = None
        if bounds is not None:
            win = from_bounds(*bounds, transform=src.transform)
            row0 = max(0, int(np.floor(win.row_off)) - 1)
            col0 = max(0, int(np.floor(win.col_off)) - 1)
            row1 = min(src.height, int(np.ceil(win.row_off + win.height)) + 1)
            col1 = min(src.width, int(np.ceil(win.col_off + win.width)) + 1)
            if row1 <= row0 or col1 <= col0:
                raise ValueError("Boundaries do not overlap the exposure raster.")
            window = rasterio.windows.Window(col0, row0, col1 - col0, row1 - row0)
            transform = src.window_transform(window)
        data = src.read(1, window=window).astype('float32')
        nodata = src.nodata
        crs = src.crs
    if nodata is not None and not np.isnan(nodata):
        data[data == nodata] = np.nan
    data[data < 0.0] = 0.0
    return data, transform, crs


def load_hazard_on_grid(haz_path, dst_crs, dst_transform, dst_shape,
                        min_haz_threshold, unit='cm'):
    """
    Warp a hazard raster onto the exposure grid (WarpedVRT, as CCDR-tools),
    convert to centimetres if needed, and NaN-out cells at or below the
    minimum hazard threshold (threshold in cm).
    """
    with rasterio.open(haz_path) as src:
        if src.crs is None:
            raise ValueError(f"Hazard raster {os.path.basename(haz_path)} has no CRS.")
        vrt_options = {
            'src_crs': src.crs,
            'crs': dst_crs,
            'transform': dst_transform,
            'height': dst_shape[0],
            'width': dst_shape[1],
            'resampling': Resampling.nearest,
        }
        with rasterio.vrt.WarpedVRT(src, **vrt_options) as vrt:
            haz = vrt.read(1).astype('float32')
            nodata = vrt.nodata
    if nodata is not None and not np.isnan(nodata):
        haz[haz == nodata] = np.nan
    if unit == 'm':
        haz = haz * 100.0
    # Fathom uses negative sentinel values in some products
    haz[haz < 0.0] = np.nan
    # Keep only values strictly above the threshold (as CCDR-tools)
    haz[~(haz > min_haz_threshold)] = np.nan
    return haz


def run_flood_analysis(
    adm_gdf, code_field, name_field,
    hazard_files,             # list of {'path': ..., 'rp': int}
    exp_path, exp_cat,        # 'POP' | 'BU' | 'AGR'
    analysis_type,            # 'Function' | 'Classes'
    min_haz_threshold=20.0,   # cm
    class_edges=None,         # cm, ascending
    wb_region='Other',
    hazard_unit='cm',         # 'cm' | 'm'
    all_touched=False,        # pixel selection for the per-RP zonal statistics
    progress=None,            # callable(pct: int, message: str)
):
    """Returns (result_gdf, summary_df, prob_df)."""
    def report(pct, msg):
        if progress:
            progress(pct, msg)

    valid_RPs = sorted({int(h['rp']) for h in hazard_files})
    rp_to_path = {int(h['rp']): h['path'] for h in hazard_files}
    n_valid_RPs_gt_1 = len(valid_RPs) > 1

    if analysis_type == 'Classes':
        if not class_edges:
            raise ValueError("Class edges must be provided for Classes analysis")
        class_edges = [float(c) for c in class_edges]
        if not np.all(np.diff(class_edges) > 0):
            raise ValueError("Class thresholds must be strictly increasing.")
        bin_seq = class_edges + [np.inf]
        num_bins = len(bin_seq)
    else:
        bin_seq, num_bins = None, None

    report(2, "Preparing administrative boundaries…")
    adm_gdf = adm_gdf.copy()
    if adm_gdf.crs is None:
        raise ValueError("Boundaries have no CRS (.prj missing?).")

    report(5, "Loading exposure raster…")
    with rasterio.open(exp_path) as src:
        exp_crs = src.crs
    if exp_crs is None:
        raise ValueError("Exposure raster has no CRS.")
    adm_in_exp = adm_gdf.to_crs(exp_crs)
    bounds = adm_in_exp.total_bounds
    exp_data, transform, exp_crs = load_exposure(exp_path, bounds=bounds)
    dst_shape = exp_data.shape
    geoms = list(adm_in_exp.geometry.values)

    report(12, "Computing total exposure per admin unit…")
    total_exp = zonal_sum(exp_data, geoms, transform, all_touched=True)

    result_df = pd.DataFrame({
        code_field: adm_gdf[code_field].values,
        name_field: adm_gdf[name_field].values,
        f"ADM_{exp_cat}": total_exp,
    })

    prob_df = exceedance_freq_table(valid_RPs)
    damage_factor = get_damage_function(exp_cat)

    # Per-RP computation
    n_rp = len(valid_RPs)
    summary_rows = []
    for k, rp in enumerate(valid_RPs):
        base_pct = 15 + int(70 * k / n_rp)
        report(base_pct, f"Processing return period 1-in-{rp}…")
        haz_data = load_hazard_on_grid(
            rp_to_path[rp], exp_crs, transform, dst_shape,
            min_haz_threshold, unit=hazard_unit)

        if analysis_type == 'Function':
            factor = damage_factor(haz_data, wb_region)
            affected_exp = np.where(factor > 0, exp_data, np.nan)
            report(base_pct + 2, f"RP {rp}: zonal statistics (exposed)…")
            result_df[f"RP{rp}_{exp_cat}_exp"] = zonal_sum(affected_exp, geoms, transform, all_touched=all_touched)
            impact_exp = affected_exp * factor
            report(base_pct + 4, f"RP {rp}: zonal statistics (impact)…")
            result_df[f"RP{rp}_{exp_cat}_imp"] = zonal_sum(impact_exp, geoms, transform, all_touched=all_touched)
            summary_rows.append({
                'RP': rp,
                f'{exp_cat}_exposed': float(np.nansum(result_df[f"RP{rp}_{exp_cat}_exp"])),
                f'{exp_cat}_impact': float(np.nansum(result_df[f"RP{rp}_{exp_cat}_imp"])),
            })
            del factor, affected_exp, impact_exp
        else:  # Classes
            with np.errstate(invalid='ignore'):
                bin_idx = np.digitize(np.nan_to_num(haz_data, nan=-9999.0), bin_seq).astype('int32')
                bin_idx[~np.isfinite(haz_data)] = num_bins + 1  # NaN cells -> out of range
            affected_exp = np.where(haz_data > 0, exp_data, np.nan)
            for bin_x in reversed(range(num_bins)):
                report(base_pct + 2, f"RP {rp}: class C{bin_x} zonal statistics…")
                class_exp = np.where(bin_idx == bin_x, affected_exp, np.nan)
                col = f"RP{rp}_{exp_cat}_C{bin_x}_exp"
                result_df[col] = zonal_sum(class_exp, geoms, transform, all_touched=all_touched)
                # Cumulative: each class includes all higher classes (as CCDR)
                if bin_x < (num_bins - 1):
                    result_df[col] = result_df[col] + result_df[f"RP{rp}_{exp_cat}_C{bin_x + 1}_exp"]
                del class_exp
            row = {'RP': rp}
            for bin_x in range(num_bins):
                row[f'{exp_cat}_C{bin_x}_exposed'] = float(np.nansum(result_df[f"RP{rp}_{exp_cat}_C{bin_x}_exp"]))
            summary_rows.append(row)
            del bin_idx, affected_exp

        del haz_data
        gc.collect()

    result_df = result_df.replace(np.nan, 0)

    # EAI / EAE across return periods (LB, UB, Mean) — as CCDR calc_EAEI
    report(88, "Computing expected annual impact / exposure…")
    for method in ('LB', 'UB', 'Mean'):
        if not n_valid_RPs_gt_1:
            break
        if analysis_type == 'Function':
            tmp = np.zeros(len(result_df))
            for rp in valid_RPs:
                freq = float(prob_df.loc[prob_df['RPs'] == rp, f'prob_RPs_{method}'].iloc[0])
                tmp = tmp + result_df[f"RP{rp}_{exp_cat}_imp"].values * freq
            result_df[f"{exp_cat}_EAI_{method}"] = tmp
            with np.errstate(divide='ignore', invalid='ignore'):
                pct = np.where(result_df[f"ADM_{exp_cat}"].values > 0,
                               tmp / result_df[f"ADM_{exp_cat}"].values * 100.0, 0.0)
            result_df[f"{exp_cat}_EAI%_{method}"] = pct
        else:
            for bin_x in reversed(range(num_bins)):
                tmp = np.zeros(len(result_df))
                for rp in valid_RPs:
                    freq = float(prob_df.loc[prob_df['RPs'] == rp, f'prob_RPs_{method}'].iloc[0])
                    tmp = tmp + result_df[f"RP{rp}_{exp_cat}_C{bin_x}_exp"].values * freq
                result_df[f"{exp_cat}_C{bin_x}_EAE_{method}"] = tmp
                with np.errstate(divide='ignore', invalid='ignore'):
                    pct = np.where(result_df[f"ADM_{exp_cat}"].values > 0,
                                   tmp / result_df[f"ADM_{exp_cat}"].values * 100.0, 0.0)
                result_df[f"{exp_cat}_C{bin_x}_EAE%_{method}"] = pct

    result_df = result_df.round(3)
    # Report the Mean estimate under the plain name (CCDR renames '_Mean' -> '')
    result_df.columns = [c.replace('_Mean', '') for c in result_df.columns]

    # Summary table (national totals), as CCDR create_summary_df
    summary_df = pd.DataFrame(summary_rows).sort_values('RP').reset_index(drop=True)
    summary_df.insert(1, 'Freq', 1.0 / summary_df['RP'])
    ex_freq = summary_df['Freq'].diff().abs().shift(-1)
    ex_freq.iloc[-1] = summary_df['Freq'].iloc[-1]
    summary_df.insert(2, 'Ex_freq', ex_freq)
    if analysis_type == 'Function' and n_valid_RPs_gt_1:
        summary_df[f'{exp_cat}_EAI'] = summary_df[f'{exp_cat}_impact'] * summary_df['Ex_freq']
    summary_df = summary_df.round(3)

    report(95, "Building output layers…")
    result_gdf = gpd.GeoDataFrame(result_df, geometry=adm_gdf.geometry.values, crs=adm_gdf.crs)
    result_gdf = result_gdf.to_crs(epsg=4326)

    report(100, "Analysis complete.")
    return result_gdf, summary_df, prob_df
