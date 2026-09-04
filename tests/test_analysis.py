"""
Tests for the flood risk analysis engine.

Run:  python3 tests/test_analysis.py
(Generates the sample dataset first if it is missing.)
"""
import os
import subprocess
import sys

import geopandas as gpd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))

from analysis import (detect_return_period, exceedance_freq_table,  # noqa: E402
                      run_flood_analysis, zonal_sum)
from damage_functions import (FL_damage_factor_agri,  # noqa: E402
                              FL_damage_factor_builtup, FL_mortality_factor)

DATA = os.path.join(HERE, "sample_data")
RPS = [5, 10, 20, 50, 100, 200, 500, 1000]
failures = []


def check(cond, label):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def ensure_data():
    if not os.path.exists(os.path.join(DATA, "population.tif")):
        subprocess.run([sys.executable, os.path.join(HERE, "make_sample_data.py")], check=True)


def test_rp_detection():
    print("\nReturn-period detection")
    for name, expected in [("1in100.tif", 100), ("1in5.tif", 5), ("RP_50.tif", 50),
                           ("rp200.tif", 200), ("flood_1000yr.tif", 1000),
                           ("no_number.tif", None)]:
        check(detect_return_period(name) == expected, f"{name} -> {expected}")


def test_exceedance_frequencies():
    """Weights must match CCDR-tools' construction exactly."""
    print("\nExceedance frequencies")
    df = exceedance_freq_table(RPS)
    prob = 1.0 / np.array(RPS, dtype=float)
    check(np.allclose(df["prob_RPs"], prob), "prob = 1/RP")
    check(np.allclose(df["prob_RPs_LB"], np.append(-np.diff(prob), prob[-1])), "lower bound weights")
    check(np.allclose(df["prob_RPs_UB"], np.insert(-np.diff(prob), 0, 0.0)), "upper bound weights")
    check(np.allclose(df["prob_RPs_Mean"], (df["prob_RPs_LB"] + df["prob_RPs_UB"]) / 2), "mean weights")
    # LB weights partition the whole probability space up to the most frequent RP
    check(abs(df["prob_RPs_LB"].sum() - 1.0 / min(RPS)) < 1e-12, "LB weights sum to 1/RP_min")


def test_damage_functions():
    """Impact factors stay in [0,1] and increase with depth."""
    print("\nDamage functions")
    depths = np.array([0, 20, 50, 100, 200, 400, 1000], dtype="float32")  # cm
    for label, fn, region in [("POP mortality", FL_mortality_factor, None),
                              ("BU damage (AFR)", FL_damage_factor_builtup, "AFR"),
                              ("BU damage (GLOBAL)", FL_damage_factor_builtup, "Other"),
                              ("AGR damage (ASIA)", FL_damage_factor_agri, "EAP"),
                              ("AGR damage (AFR)", FL_damage_factor_agri, "AFR")]:
        v = np.asarray(fn(depths, region), dtype="float64")
        check(np.all(np.isfinite(v)), f"{label}: finite")
        check(np.all((v >= 0) & (v <= 1)), f"{label}: within [0,1]")
        check(np.all(np.diff(v) >= -1e-6), f"{label}: non-decreasing with depth")
    # A metre of water should kill a non-trivial share of exposed people
    check(0.0 < float(FL_mortality_factor(np.array([100.0]))[0]) < 0.1,
          "POP mortality at 1 m is small but non-zero")


def test_zonal_sum():
    """Zonal sums must reproduce a plain masked sum, and respect all_touched."""
    print("\nZonal statistics")
    from rasterio.transform import from_origin
    from shapely.geometry import box
    arr = np.ones((10, 10), dtype="float32")
    arr[0, 0] = np.nan
    t = from_origin(0, 10, 1, 1)
    whole = box(0, 0, 10, 10)
    check(abs(zonal_sum(arr, [whole], t)[0] - 99.0) < 1e-6, "sum over full extent ignores NaN")
    half = box(0, 0, 5, 10)
    check(abs(zonal_sum(arr, [half], t)[0] - 49.0) < 1e-6, "sum over half extent")
    # Sub-pixel polygon sitting inside one cell but clear of its centre (2.5, 2.5)
    tiny = box(2.05, 2.05, 2.35, 2.35)
    check(zonal_sum(arr, [tiny], t, all_touched=False)[0] == 0.0, "sub-pixel polygon: centre mode = 0")
    check(zonal_sum(arr, [tiny], t, all_touched=True)[0] == 1.0, "sub-pixel polygon: all_touched = 1")
    outside = box(100, 100, 110, 110)
    check(zonal_sum(arr, [outside], t)[0] == 0.0, "polygon outside raster -> 0")


def test_function_analysis():
    print("\nFunction (EAI) analysis")
    adm = gpd.read_file(os.path.join(DATA, "boundaries.shp"))
    haz = [{"path": os.path.join(DATA, f"1in{rp}.tif"), "rp": rp} for rp in RPS]
    gdf, summary, prob = run_flood_analysis(
        adm, "HASC_2", "NAM_2", haz, os.path.join(DATA, "population.tif"),
        "POP", "Function", min_haz_threshold=20.0, wb_region="AFR")

    check(len(gdf) == len(adm), "one row per admin unit")
    check("POP_EAI" in gdf.columns and "POP_EAI%" in gdf.columns, "EAI columns present")
    check(all(f"POP_EAI_{m}" in gdf.columns for m in ("LB", "UB")), "LB/UB columns kept")
    check(str(gdf.crs).upper().endswith("4326"), "output reprojected to EPSG:4326")

    # EAI must equal sum over RPs of impact * mean exceedance frequency
    manual = sum(gdf[f"RP{rp}_POP_imp"].values *
                 float(prob.loc[prob.RPs == rp, "prob_RPs_Mean"].iloc[0]) for rp in RPS)
    check(np.allclose(gdf["POP_EAI"].values, manual, atol=0.02), "EAI = Σ impact × exceedance freq")

    imp = [float(gdf[f"RP{rp}_POP_imp"].sum()) for rp in RPS]
    check(all(b >= a - 1e-6 for a, b in zip(imp, imp[1:])), "impact increases with return period")
    check(bool((gdf["RP100_POP_imp"] <= gdf["RP100_POP_exp"] + 1e-6).all()), "impact <= exposed")
    check(bool((gdf["RP100_POP_exp"] <= gdf["ADM_POP"] + 1e-6).all()), "exposed <= total exposure")
    check(bool((gdf["POP_EAI"] >= 0).all()), "EAI non-negative")
    check(len(summary) == len(RPS), "summary has one row per return period")
    check("POP_EAI" in summary.columns, "summary carries EAI")


def test_classes_analysis():
    print("\nClasses (EAE) analysis")
    adm = gpd.read_file(os.path.join(DATA, "boundaries.shp"))
    haz = [{"path": os.path.join(DATA, f"1in{rp}.tif"), "rp": rp} for rp in RPS]
    gdf, summary, _ = run_flood_analysis(
        adm, "HASC_2", "NAM_2", haz, os.path.join(DATA, "population.tif"),
        "POP", "Classes", min_haz_threshold=20.0, class_edges=[50, 100, 150], wb_region="AFR")
    for bin_x in range(4):
        check(f"POP_C{bin_x}_EAE" in gdf.columns, f"class C{bin_x} EAE column")
    # Classes are cumulative: C0 >= C1 >= C2 >= C3
    for rp in (100, 1000):
        vals = [float(gdf[f"RP{rp}_POP_C{b}_exp"].sum()) for b in range(4)]
        check(all(a >= b - 1e-6 for a, b in zip(vals, vals[1:])),
              f"RP{rp} classes are cumulative ({[round(v) for v in vals]})")


def test_threshold_and_units():
    print("\nThreshold and unit handling")
    adm = gpd.read_file(os.path.join(DATA, "boundaries.shp"))
    haz = [{"path": os.path.join(DATA, f"1in{rp}.tif"), "rp": rp} for rp in RPS]
    exp = os.path.join(DATA, "population.tif")
    low, _, _ = run_flood_analysis(adm, "HASC_2", "NAM_2", haz, exp, "POP",
                                   "Function", min_haz_threshold=0.0, wb_region="AFR")
    high, _, _ = run_flood_analysis(adm, "HASC_2", "NAM_2", haz, exp, "POP",
                                    "Function", min_haz_threshold=150.0, wb_region="AFR")
    check(float(low["POP_EAI"].sum()) > float(high["POP_EAI"].sum()),
          "a higher hazard threshold yields lower EAI")

    # Reading cm data as metres inflates depths 100x, so impact must rise
    as_m, _, _ = run_flood_analysis(adm, "HASC_2", "NAM_2", haz, exp, "POP",
                                    "Function", min_haz_threshold=20.0,
                                    wb_region="AFR", hazard_unit="m")
    check(float(as_m["POP_EAI"].sum()) > float(low["POP_EAI"].sum()),
          "metre units scale depths up")


def test_single_return_period():
    print("\nSingle return period")
    adm = gpd.read_file(os.path.join(DATA, "boundaries.shp"))
    haz = [{"path": os.path.join(DATA, "1in100.tif"), "rp": 100}]
    gdf, summary, _ = run_flood_analysis(
        adm, "HASC_2", "NAM_2", haz, os.path.join(DATA, "population.tif"),
        "POP", "Function", min_haz_threshold=20.0, wb_region="AFR")
    # Deterministic run: per-RP results only, no EAI (matches CCDR-tools)
    check("RP100_POP_imp" in gdf.columns, "per-RP impact present")
    check("POP_EAI" not in gdf.columns, "no EAI for a single return period")
    check(len(summary) == 1, "summary has one row")


def test_validation_errors():
    print("\nInput validation")
    adm = gpd.read_file(os.path.join(DATA, "boundaries.shp"))
    haz = [{"path": os.path.join(DATA, "1in100.tif"), "rp": 100}]
    exp = os.path.join(DATA, "population.tif")
    for label, kwargs in [
        ("non-increasing class edges", dict(analysis_type="Classes", class_edges=[100, 50])),
        ("missing class edges", dict(analysis_type="Classes", class_edges=None)),
    ]:
        try:
            run_flood_analysis(adm, "HASC_2", "NAM_2", haz, exp, "POP",
                               kwargs.pop("analysis_type"), **kwargs)
            check(False, f"{label} raises")
        except ValueError:
            check(True, f"{label} raises")


if __name__ == "__main__":
    ensure_data()
    for fn in (test_rp_detection, test_exceedance_frequencies, test_damage_functions,
               test_zonal_sum, test_function_analysis, test_classes_analysis,
               test_threshold_and_units, test_single_return_period, test_validation_errors):
        fn()
    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print("All tests passed.")
