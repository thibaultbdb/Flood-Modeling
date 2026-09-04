# Flood Risk Mapping Platform

A self-contained web application for probabilistic flood risk analysis, replicating the
flood (FL) workflow of the World Bank / GFDRR
[CCDR-tools](https://github.com/GFDRR/CCDR-tools) notebook.

Upload your **Fathom v3 flood depth rasters**, an **exposure raster** and your
**administrative boundaries**, choose the settings, and get Expected Annual Impact
(EAI) or Expected Annual Exposure (EAE) per administrative unit — as an interactive
map, charts, and downloadable tables.

![Results view: choropleth map, impact-vs-return-period chart and result tables](docs/screenshot.png)

<details>
<summary>Input steps</summary>

![Upload steps: boundaries, hazard rasters with auto-detected return periods, exposure](docs/screenshot-inputs.png)

</details>

## Why this exists

The CCDR-tools flood analysis is a Jupyter notebook with a heavy geospatial stack
(dask, multiprocess, rioxarray, rasterstats) that expects a specific on-disk data
tree (`DATA/HZD/<ISO3>/<hazard>/<period>/<scenario>/1in<RP>.tif`) and downloads its
own boundaries and exposure layers. That makes it awkward to run against data you
already have. This platform keeps the **same methodology and the same equations**
but replaces the notebook with a browser UI where every input is simply uploaded.

## Quick start

```bash
pip install -r requirements.txt
./run.sh                 # -> http://127.0.0.1:8000
```

No data to hand? Generate a synthetic sample set and try the whole flow:

```bash
python3 tests/make_sample_data.py
# then upload from tests/sample_data/:
#   boundaries.zip   -> step 1
#   1in*.tif         -> step 2
#   population.tif   -> step 3
```

## Inputs

| Step | Input | Format | Notes |
|---|---|---|---|
| 1 | Administrative boundaries | zipped shapefile (`.zip` containing `.shp`/`.dbf`/`.shx`/`.prj`), GeoJSON or GeoPackage | Must carry a CRS. You then pick the *code* and *name* attribute fields (auto-guessed). |
| 2 | Flood hazard | one GeoTIFF per return period | Return periods are auto-detected from filenames (`1in100.tif`, `RP_100.tif`, `100yr.tif`) and editable in the table. |
| 3 | Exposure | GeoTIFF | Population count, built-up area, or agricultural land. |

**Fathom v3 layout.** Fathom ships one file per return period per scenario, e.g.
`FLUVIAL_UNDEFENDED/2020/1in5.tif … 1in1000.tif`. Upload the set for the single
hazard type / period / scenario you want to analyse; run the tool again for each
other combination. Depths are in **centimetres** in Fathom v3 — that is the default;
switch the *Depth unit* selector to metres if your rasters are already converted.

Hazard rasters do **not** need to match the exposure grid or CRS: each is warped onto
the exposure grid on the fly (`WarpedVRT`, nearest-neighbour), exactly as CCDR-tools
does. The exposure raster's grid therefore defines the analysis resolution.

## Settings

- **Approach**
  - *Impact function (EAI)* — converts depth to an impact fraction via a
    vulnerability curve, giving impacted exposure and Expected Annual Impact.
  - *Hazard classes (EAE)* — bins depth into user-defined classes and reports
    exposure per class and Expected Annual Exposure. Classes are **cumulative**:
    C1 counts everything at or above edge 1, so C0 ≥ C1 ≥ C2 ≥ …
- **Minimum hazard threshold** (default 20 cm) — depths at or below this are treated
  as no hazard, the CCDR-tools default.
- **World Bank region** — selects the regional damage curve for built-up and
  agriculture. Population mortality uses a single global curve, so this is hidden
  for population.
- **Pixel selection** — *Cell centre* (rasterstats' default, what CCDR-tools uses for
  its per-return-period statistics) or *All touched*. Use *All touched* when admin
  units are only a few pixels across.

## Methodology

Ported from `tools/code/runAnalysis.py` and `tools/code/damageFunctions.py`.

**1. Impact per return period.** For each return period *i*, hazard depth is warped
onto the exposure grid and thresholded. Under the function approach the depth *d*
becomes an impact factor *F(d) ∈ [0,1]*, and per admin unit:

```
exposed_i  = Σ  exposure(px)              over pixels where F(d) > 0
impact_i   = Σ  exposure(px) · F(d(px))   over the same pixels
```

**2. Exceedance frequency.** With return periods RP₁ < RP₂ < … < RPₙ and
probabilities *pᵢ = 1/RPᵢ*, CCDR-tools assigns each return period a frequency
weight, computed three ways:

```
LB_i    = p_i − p_{i+1}          (last: p_n)
UB_i    = p_{i−1} − p_i          (first: 0)
Mean_i  = (LB_i + UB_i) / 2
```

**3. Expected Annual Impact.** The impact–frequency curve is integrated:

```
EAI  = Σ_i  impact_i · freq_i
EAI% = EAI / total exposure × 100
```

The **Mean** estimate is reported as `POP_EAI` / `BU_EAI` / `AGR_EAI`; the lower and
upper bounds are kept alongside as `*_EAI_LB` and `*_EAI_UB` so you can see the
integration uncertainty. The class approach substitutes exposure per class for
impact and produces `*_C<n>_EAE` in the same way.

With a **single** return period the calculation is deterministic — per-return-period
results only, no EAI — matching CCDR-tools.

### Vulnerability curves

Reproduced verbatim from CCDR-tools (`damageFunctions.py`); depth *x* in metres:

| Exposure | Curve | Source |
|---|---|---|
| Population (`POP`) | `0.985 / (1 + e^(6.32 − 1.412x))` | Jonkman (2008), [doi:10.1111/j.1753-318X.2008.00006.x](https://doi.org/10.1111/j.1753-318X.2008.00006.x) |
| Built-up (`BU`) | regional sigmoid (Africa / Asia / LAC / Global) | Huizinga et al. (2017), EU-JRC |
| Agriculture (`AGR`) | regional sigmoid (Africa / Asia / LAC / Global) | Huizinga et al. (2017), EU-JRC |

## Outputs

Per administrative unit: total exposure, exposed and impacted exposure for every
return period, and EAI/EAE with LB/UB bounds and percentages.

- **Excel** — `Results`, `Summary` (whole-area totals per return period) and
  `Exceedance_freq` sheets
- **CSV** — the results table
- **GeoPackage** / **GeoJSON** — the same table with geometry, ready for QGIS

In the browser you also get a quantile-classed choropleth (switchable between every
output indicator), per-unit popups, the impact-vs-return-period chart, and sortable
tables.

## Differences from the CCDR-tools notebook

Deliberate, and worth knowing:

1. **You supply all inputs.** The notebook downloads boundaries (GADM/WB) and default
   exposure (WorldPop/WSF/ESA) by ISO3 country code. Here everything is uploaded, so
   the tool works for any area of interest, including non-country study areas.
2. **Flood only.** Tropical cyclones, the custom-hazard and bivariate workflows, and
   the climate-scenario folder conventions are not included, as requested.
3. **Zonal statistics** are computed per feature with an explicit rasterised mask
   rather than through `rasterstats`. Results are equivalent, and this handles
   multipart geometries (islands, enclaves) directly — so the notebook's
   explode-and-reaggregate workaround for nested multiparts is unnecessary.
4. **Single-process.** No dask/multiprocess. The exposure raster is windowed to the
   boundary extent, which keeps typical national runs comfortable in memory, but a
   very large area at 30 m will be slower than the parallel notebook.
5. `all_touched` is exposed as a setting. CCDR-tools is internally inconsistent here
   (`True` for admin totals, rasterstats' default `False` for per-return-period
   stats); the default reproduces that behaviour exactly.

## Project layout

```
app/
  main.py              FastAPI backend: uploads, jobs, downloads
  analysis.py          analysis engine (the CCDR-tools port)
  damage_functions.py  vulnerability curves, ported verbatim
  static/              UI (vanilla JS + Leaflet + Chart.js, all vendored)
tests/
  test_analysis.py     engine test suite
  make_sample_data.py  synthetic Fathom-like dataset generator
run.sh                 launcher
```

The frontend dependencies (Leaflet 1.9.4, Chart.js 4.4.4) are **vendored** under
`app/static/vendor/`, so the application runs with no internet access — which
matters when handling licensed Fathom data on a closed network. Only the basemap
tiles need connectivity; the map has a *None (offline)* basemap option, and analysis
never requires network access.

## Notes and limits

- Results are stored per session under `data/` and are not authenticated. This is
  built as a **local / trusted-network analysis tool**; don't expose it to the open
  internet without adding authentication and upload limits.
- The upload path holds the whole exposure raster window in memory. For very large
  rasters, clip to your area of interest first.
- Boundaries are capped at 5000 features to keep the browser map responsive.

## Licence and attribution

The methodology and the vulnerability curves come from
[GFDRR/CCDR-tools](https://github.com/GFDRR/CCDR-tools) (World Bank / GFDRR).
Fathom v3 flood data is licensed separately — this tool never transmits your data
anywhere; all processing happens on the machine running the server.
