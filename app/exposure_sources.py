"""
Automatic exposure download, as CCDR-tools does it.

The CCDR-tools notebook fetches exposure by ISO3 country code rather than
asking the user for a file (tools/code/input_utils.py). This reproduces that
for population, so an analysis needs only hazard rasters and boundaries.

Population comes from the WorldPop Global Constrained 2020 UN-adjusted
100 m product -- the same dataset and the same URL pattern CCDR-tools uses:

    https://data.worldpop.org/GIS/Population/
        Global_2000_2020_Constrained/2020/BSGM/{ISO3}/
        {iso3}_ppp_2020_UNadj_constrained.tif

Note on agriculture: CCDR-tools' fetch_agri_data() downloads this same
population dataset and merely saves it under an _AGR name, so its "agriculture"
exposure is mislabelled population. That is not reproduced here -- for built-up
or agricultural exposure, upload your own raster (GHSL/WSF, ESA WorldCover).
"""
import os
import re

import requests

# Overridable so the download can be pointed at a local mirror or a test server
WORLDPOP_BASE = os.environ.get(
    "FRM_WORLDPOP_BASE", "https://data.worldpop.org/GIS/Population/")
WORLDPOP_TEMPLATE = (
    "Global_2000_2020_Constrained/{year}/BSGM/{ISO3}/"
    "{iso3}_ppp_{year}_UNadj_constrained.tif"
)
WORLDPOP_YEARS = ["2020"]
ISO3_RE = re.compile(r"^[A-Za-z]{3}$")


class ExposureFetchError(RuntimeError):
    """Raised when exposure data could not be downloaded."""


def worldpop_population_url(iso3, year="2020"):
    iso3 = iso3.strip()
    if not ISO3_RE.match(iso3):
        raise ExposureFetchError(
            f"'{iso3}' is not a 3-letter ISO country code (e.g. NGA, BGD, MOZ).")
    path = WORLDPOP_TEMPLATE.format(ISO3=iso3.upper(), iso3=iso3.lower(), year=year)
    return WORLDPOP_BASE + path


def fetch_worldpop_population(iso3, dest_path, year="2020", progress=None,
                              session=None, timeout=60):
    """
    Download the WorldPop constrained population raster for `iso3`.

    `progress` is called as progress(downloaded_bytes, total_bytes_or_None).
    Returns the path written. Raises ExposureFetchError with an actionable
    message on any failure; a partial download is removed rather than left
    behind to be mistaken for a complete file.
    """
    url = worldpop_population_url(iso3, year)
    http = session or requests
    tmp_path = dest_path + ".part"
    try:
        with http.get(url, stream=True, timeout=timeout) as r:
            if r.status_code == 404:
                raise ExposureFetchError(
                    f"WorldPop has no {year} population raster for '{iso3.upper()}'. "
                    f"Check the ISO3 code, or upload an exposure raster instead.")
            r.raise_for_status()
            total = r.headers.get("Content-Length")
            total = int(total) if total and total.isdigit() else None
            done = 0
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if os.path.getsize(tmp_path) == 0:
            raise ExposureFetchError("WorldPop returned an empty file.")
        os.replace(tmp_path, dest_path)
        return dest_path
    except ExposureFetchError:
        _cleanup(tmp_path)
        raise
    except requests.exceptions.Timeout:
        _cleanup(tmp_path)
        raise ExposureFetchError(
            "Timed out contacting data.worldpop.org. Check your connection "
            "(or a proxy/firewall) and retry, or upload a raster instead.")
    except requests.exceptions.RequestException as e:
        _cleanup(tmp_path)
        raise ExposureFetchError(
            f"Could not download from data.worldpop.org ({e}). "
            f"Upload an exposure raster instead if the host is unreachable.")


def _cleanup(path):
    try:
        os.remove(path)
    except OSError:
        pass
