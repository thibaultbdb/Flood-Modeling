"""
Generate a small synthetic Fathom-like dataset so the platform can be tried
without licensed data.

    python3 tests/make_sample_data.py [output_dir]

Produces 1in{5..1000}.tif depth rasters (cm), population.tif and a
16-unit boundaries shapefile, all on a ~100 m grid.
"""
import os
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import box

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sample_data")
os.makedirs(OUT, exist_ok=True)

# 200x200 grid at ~0.001 deg (~100m), origin at (10.0, 5.0)
W = H = 200
res = 0.001
transform = from_origin(10.0, 5.0, res, res)
crs = "EPSG:4326"

rng = np.random.default_rng(42)

# Exposure: population count, denser in the middle
yy, xx = np.mgrid[0:H, 0:W]
pop = (50 * np.exp(-(((xx - 100) ** 2 + (yy - 100) ** 2) / (2 * 60.0 ** 2)))).astype("float32")
pop += rng.random((H, W)).astype("float32") * 2
with rasterio.open(os.path.join(OUT, "population.tif"), "w", driver="GTiff",
                   height=H, width=W, count=1, dtype="float32",
                   crs=crs, transform=transform, nodata=-9999.0) as dst:
    dst.write(pop, 1)

# Hazard: a river band, depth grows with return period (cm)
RPS = [5, 10, 20, 50, 100, 200, 500, 1000]
dist_to_river = np.abs(yy - 100).astype("float32")
for rp in RPS:
    width_px = 5 + 3.5 * np.log(rp)         # wider flooding for rarer events
    peak = 40 + 55 * np.log10(rp)           # deeper for rarer events (cm)
    depth = peak * np.maximum(0.0, 1 - dist_to_river / width_px)
    depth[depth <= 0] = 0.0
    with rasterio.open(os.path.join(OUT, f"1in{rp}.tif"), "w", driver="GTiff",
                       height=H, width=W, count=1, dtype="float32",
                       crs=crs, transform=transform, nodata=0.0) as dst:
        dst.write(depth.astype("float32"), 1)

# Boundaries: 4x4 grid of admin units
cells, codes, names = [], [], []
for i in range(4):
    for j in range(4):
        x0 = 10.0 + i * (W * res / 4)
        y1 = 5.0 - j * (H * res / 4)
        cells.append(box(x0, y1 - (H * res / 4), x0 + (W * res / 4), y1))
        codes.append(f"ADM2_{i}{j}")
        names.append(f"District {i}-{j}")
gdf = gpd.GeoDataFrame({"HASC_2": codes, "NAM_2": names, "geometry": cells}, crs=crs)
gdf.to_file(os.path.join(OUT, "boundaries.shp"))

# Zip the shapefile so it can be uploaded straight into the web UI
import zipfile
with zipfile.ZipFile(os.path.join(OUT, "boundaries.zip"), "w") as zf:
    for ext in (".shp", ".dbf", ".shx", ".prj", ".cpg"):
        f = os.path.join(OUT, "boundaries" + ext)
        if os.path.exists(f):
            zf.write(f, "boundaries" + ext)

print("Test data written to", OUT)
print("Total population:", float(pop.sum()))
