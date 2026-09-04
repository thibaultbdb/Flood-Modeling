"""
Flood Risk Mapping Platform — web backend.

A user-friendly web replica of the flood-risk branch of GFDRR CCDR-tools:
upload Fathom v3 hazard rasters + admin boundaries (shapefile) + an
exposure raster, choose the analysis settings, and get EAI/EAE results
per admin unit as an interactive map, charts and downloadable tables.

Run with:  uvicorn main:app --host 0.0.0.0 --port 8000   (from the app/ dir)
"""
import io
import json
import os
import re
import shutil
import threading
import traceback
import uuid
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from analysis import detect_return_period, run_flood_analysis

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("FRM_DATA_DIR", os.path.join(BASE_DIR, "..", "data"))
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="Flood Risk Mapping Platform")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

SESSIONS = {}   # sid -> dict(dir, boundaries, hazards, exposure)
JOBS = {}       # job_id -> dict(status, progress, message, error, results…)

MAX_PREVIEW_FEATURES = 5000


def _session(sid):
    if sid not in SESSIONS:
        raise HTTPException(404, "Unknown session — upload boundaries first.")
    return SESSIONS[sid]


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", os.path.basename(name))


def _simplified_geojson(gdf, max_coords=200_000):
    """GeoJSON in EPSG:4326, simplified enough to stay light in the browser."""
    g = gdf.to_crs(epsg=4326)
    minx, miny, maxx, maxy = g.total_bounds
    span = max(maxx - minx, maxy - miny, 1e-6)
    tol = span / 2000.0
    g = g.copy()
    g["geometry"] = g.geometry.simplify(tol, preserve_topology=True)
    return json.loads(g.to_json()), [float(minx), float(miny), float(maxx), float(maxy)]


# ---------------------------------------------------------------- session ---

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(BASE_DIR, "static", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/favicon.ico")
def favicon():
    return FileResponse(os.path.join(BASE_DIR, "static", "favicon.svg"),
                        media_type="image/svg+xml")


@app.post("/api/session")
def create_session():
    sid = uuid.uuid4().hex[:12]
    sdir = os.path.join(DATA_DIR, "sessions", sid)
    os.makedirs(sdir, exist_ok=True)
    SESSIONS[sid] = {"dir": sdir, "boundaries": None, "hazards": [], "exposure": None}
    return {"session_id": sid}


# ------------------------------------------------------------- boundaries ---

@app.post("/api/upload/boundaries")
async def upload_boundaries(session_id: str = Form(...), file: UploadFile = File(...)):
    sess = _session(session_id)
    bdir = os.path.join(sess["dir"], "boundaries")
    shutil.rmtree(bdir, ignore_errors=True)
    os.makedirs(bdir)

    fname = _safe_name(file.filename or "boundaries")
    raw = await file.read()
    ext = os.path.splitext(fname)[1].lower()

    try:
        if ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for member in zf.namelist():
                    mname = _safe_name(member)
                    if not mname or member.endswith("/"):
                        continue
                    with zf.open(member) as src, open(os.path.join(bdir, mname), "wb") as dst:
                        shutil.copyfileobj(src, dst)
            shp = [f for f in os.listdir(bdir) if f.lower().endswith(".shp")]
            if not shp:
                raise HTTPException(400, "No .shp file found inside the zip archive.")
            path = os.path.join(bdir, shp[0])
        elif ext in (".geojson", ".json", ".gpkg"):
            path = os.path.join(bdir, fname)
            with open(path, "wb") as f:
                f.write(raw)
        else:
            raise HTTPException(400, "Upload a zipped shapefile (.zip), GeoJSON or GeoPackage.")

        gdf = gpd.read_file(path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not read boundaries: {e}")

    if gdf.empty:
        raise HTTPException(400, "The boundary file contains no features.")
    if gdf.crs is None:
        raise HTTPException(400, "The boundary file has no CRS (missing .prj?). "
                                 "Please provide a projected file (EPSG:4326 recommended).")
    if len(gdf) > MAX_PREVIEW_FEATURES:
        raise HTTPException(400, f"Too many features ({len(gdf)}). Max {MAX_PREVIEW_FEATURES}.")

    # Candidate attribute fields (non-geometry)
    fields = [c for c in gdf.columns if c != gdf.geometry.name]
    preview, bounds = _simplified_geojson(gdf)

    sess["boundaries"] = {"path": path, "fields": fields, "count": len(gdf),
                          "crs": str(gdf.crs)}
    return {"fields": fields, "count": len(gdf), "crs": str(gdf.crs),
            "preview": preview, "bounds": bounds}


# ----------------------------------------------------------------- hazard ---

@app.post("/api/upload/hazard")
async def upload_hazard(session_id: str = Form(...), files: list[UploadFile] = File(...)):
    sess = _session(session_id)
    hdir = os.path.join(sess["dir"], "hazard")
    os.makedirs(hdir, exist_ok=True)

    added = []
    for up in files:
        fname = _safe_name(up.filename or "hazard.tif")
        if not fname.lower().endswith((".tif", ".tiff")):
            raise HTTPException(400, f"{fname}: hazard layers must be GeoTIFF (.tif).")
        path = os.path.join(hdir, fname)
        with open(path, "wb") as f:
            shutil.copyfileobj(up.file, f)
        try:
            with rasterio.open(path) as src:
                meta = {"crs": str(src.crs), "width": src.width, "height": src.height,
                        "res": [abs(src.transform.a), abs(src.transform.e)],
                        "nodata": src.nodata}
        except Exception as e:
            os.remove(path)
            raise HTTPException(400, f"{fname}: not a readable GeoTIFF ({e}).")
        entry = {"filename": fname, "path": path,
                 "rp": detect_return_period(fname), "meta": meta}
        # replace previous upload of the same filename
        sess["hazards"] = [h for h in sess["hazards"] if h["filename"] != fname]
        sess["hazards"].append(entry)
        added.append({k: entry[k] for k in ("filename", "rp", "meta")})

    return {"hazards": [{k: h[k] for k in ("filename", "rp", "meta")}
                        for h in sorted(sess["hazards"], key=lambda h: h["rp"] or 0)]}


@app.post("/api/hazard/remove")
def remove_hazard(session_id: str = Form(...), filename: str = Form(...)):
    sess = _session(session_id)
    fname = _safe_name(filename)
    keep = []
    for h in sess["hazards"]:
        if h["filename"] == fname:
            try:
                os.remove(h["path"])
            except OSError:
                pass
        else:
            keep.append(h)
    sess["hazards"] = keep
    return {"hazards": [{k: h[k] for k in ("filename", "rp", "meta")} for h in keep]}


# --------------------------------------------------------------- exposure ---

@app.post("/api/upload/exposure")
async def upload_exposure(session_id: str = Form(...), file: UploadFile = File(...)):
    sess = _session(session_id)
    edir = os.path.join(sess["dir"], "exposure")
    shutil.rmtree(edir, ignore_errors=True)
    os.makedirs(edir)
    fname = _safe_name(file.filename or "exposure.tif")
    if not fname.lower().endswith((".tif", ".tiff")):
        raise HTTPException(400, "The exposure layer must be a GeoTIFF (.tif).")
    path = os.path.join(edir, fname)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        with rasterio.open(path) as src:
            meta = {"crs": str(src.crs), "width": src.width, "height": src.height,
                    "res": [abs(src.transform.a), abs(src.transform.e)],
                    "nodata": src.nodata}
    except Exception as e:
        os.remove(path)
        raise HTTPException(400, f"Not a readable GeoTIFF ({e}).")
    sess["exposure"] = {"path": path, "filename": fname, "meta": meta}
    return {"filename": fname, "meta": meta}


# -------------------------------------------------------------------- run ---

@app.post("/api/run")
async def run_analysis_endpoint(payload: dict):
    sid = payload.get("session_id")
    sess = _session(sid)
    if not sess["boundaries"]:
        raise HTTPException(400, "Upload the boundaries first.")
    if not sess["hazards"]:
        raise HTTPException(400, "Upload at least one hazard raster.")
    if not sess["exposure"]:
        raise HTTPException(400, "Upload an exposure raster.")

    code_field = payload.get("code_field")
    name_field = payload.get("name_field")
    fields = sess["boundaries"]["fields"]
    if code_field not in fields or name_field not in fields:
        raise HTTPException(400, "Select valid code and name fields for the boundaries.")

    rp_map = payload.get("return_periods") or {}
    hazard_files = []
    for h in sess["hazards"]:
        rp = rp_map.get(h["filename"], h["rp"])
        if rp is None:
            raise HTTPException(400, f"No return period set for {h['filename']}.")
        hazard_files.append({"path": h["path"], "rp": int(rp)})
    rps = [h["rp"] for h in hazard_files]
    if len(set(rps)) != len(rps):
        raise HTTPException(400, "Two hazard files share the same return period.")

    exp_cat = payload.get("exp_cat", "POP")
    if exp_cat not in ("POP", "BU", "AGR"):
        raise HTTPException(400, "exp_cat must be POP, BU or AGR.")
    analysis_type = payload.get("analysis_type", "Function")
    if analysis_type not in ("Function", "Classes"):
        raise HTTPException(400, "analysis_type must be Function or Classes.")
    try:
        threshold = float(payload.get("min_haz_threshold", 20.0))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid hazard threshold.")
    class_edges = payload.get("class_edges") or []
    wb_region = payload.get("wb_region", "Other")
    hazard_unit = payload.get("hazard_unit", "cm")
    if hazard_unit not in ("cm", "m"):
        raise HTTPException(400, "hazard_unit must be 'cm' or 'm'.")
    all_touched = bool(payload.get("all_touched", False))

    job_id = uuid.uuid4().hex[:12]
    job = {"status": "running", "progress": 0, "message": "Starting…",
           "error": None, "dir": os.path.join(sess["dir"], "jobs", job_id),
           "exp_cat": exp_cat, "analysis_type": analysis_type}
    os.makedirs(job["dir"], exist_ok=True)
    JOBS[job_id] = job

    boundaries_path = sess["boundaries"]["path"]
    exposure_path = sess["exposure"]["path"]

    def progress(pct, msg):
        job["progress"] = int(pct)
        job["message"] = msg

    def worker():
        try:
            adm = gpd.read_file(boundaries_path)
            result_gdf, summary_df, prob_df = run_flood_analysis(
                adm, code_field, name_field, hazard_files,
                exposure_path, exp_cat, analysis_type,
                min_haz_threshold=threshold,
                class_edges=[float(c) for c in class_edges] if class_edges else None,
                wb_region=wb_region, hazard_unit=hazard_unit,
                all_touched=all_touched, progress=progress,
            )
            # Persist outputs
            out = job["dir"]
            base = f"flood_{analysis_type.lower()}_{exp_cat}"
            result_gdf.to_file(os.path.join(out, base + ".gpkg"), driver="GPKG")
            csv_df = result_gdf.drop(columns="geometry")
            csv_df.to_csv(os.path.join(out, base + ".csv"), index=False)
            with pd.ExcelWriter(os.path.join(out, base + ".xlsx")) as xw:
                csv_df.to_excel(xw, sheet_name="Results", index=False)
                summary_df.to_excel(xw, sheet_name="Summary", index=False)
                prob_df.to_excel(xw, sheet_name="Exceedance_freq", index=False)
            with open(os.path.join(out, base + ".geojson"), "w") as f:
                f.write(result_gdf.to_json())

            preview, bounds = _simplified_geojson(result_gdf)
            job["results"] = {
                "geojson": preview,
                "bounds": bounds,
                "summary": summary_df.replace({np.nan: None}).to_dict(orient="records"),
                "columns": [c for c in csv_df.columns],
                "code_field": code_field,
                "name_field": name_field,
                "base": base,
            }
            job["status"] = "done"
            job["progress"] = 100
            job["message"] = "Analysis complete."
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
            job["message"] = f"Failed: {e}"
            job["traceback"] = traceback.format_exc()

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    return {"status": job["status"], "progress": job["progress"],
            "message": job["message"], "error": job["error"]}


@app.get("/api/results/{job_id}")
def job_results(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    if job["status"] != "done":
        raise HTTPException(409, "Job not finished.")
    return JSONResponse(job["results"])


@app.get("/api/download/{job_id}/{fmt}")
def download(job_id: str, fmt: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(404, "No results for this job.")
    ext = {"csv": ".csv", "xlsx": ".xlsx", "gpkg": ".gpkg", "geojson": ".geojson"}.get(fmt)
    if not ext:
        raise HTTPException(400, "Format must be csv, xlsx, gpkg or geojson.")
    path = os.path.join(job["dir"], job["results"]["base"] + ext)
    if not os.path.exists(path):
        raise HTTPException(404, "File not found.")
    return FileResponse(path, filename=os.path.basename(path))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
