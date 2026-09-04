"""
Exercise the WorldPop exposure download against a local stand-in for
data.worldpop.org, so the URL construction, progress reporting, partial-file
cleanup and error messages are covered without touching the network.

Run:  python3 tests/test_exposure_fetch.py
"""
import os, sys, threading, http.server, functools, shutil, tempfile
ROOT = tempfile.mkdtemp()
# lay out a file at the exact WorldPop path structure
p = os.path.join(ROOT, "Global_2000_2020_Constrained/2020/BSGM/NGA")
os.makedirs(p)
HERE = os.path.dirname(os.path.abspath(__file__))
if not os.path.exists(os.path.join(HERE, "sample_data", "population.tif")):
    import subprocess; subprocess.run([sys.executable, os.path.join(HERE, "make_sample_data.py")], check=True)
shutil.copy(os.path.join(HERE, "sample_data", "population.tif"),
            os.path.join(p, "nga_ppp_2020_UNadj_constrained.tif"))

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8777),
        functools.partial(Quiet, directory=ROOT))
threading.Thread(target=srv.serve_forever, daemon=True).start()

os.environ["FRM_WORLDPOP_BASE"] = "http://127.0.0.1:8777/"
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import exposure_sources as ex
ex.WORLDPOP_BASE = "http://127.0.0.1:8777/"

fails = []
def check(c, l):
    print(f"  {'PASS' if c else 'FAIL'}  {l}")
    if not c: fails.append(l)

# happy path + progress callbacks
calls = []
dest = os.path.join(tempfile.mkdtemp(), "NGA_POP.tif")
out = ex.fetch_worldpop_population("NGA", dest, progress=lambda d,t: calls.append((d,t)))
check(os.path.exists(out) and os.path.getsize(out) > 1000, "downloads the raster")
check(len(calls) > 0 and calls[-1][0] == os.path.getsize(out), "reports byte progress")
check(calls[-1][1] == os.path.getsize(out), "reports the total size")
import rasterio
with rasterio.open(out) as src: check(src.count == 1, "result is a readable GeoTIFF")

# lowercase ISO3 still resolves (URL uses both cases)
out2 = ex.fetch_worldpop_population("nga", dest + "2")
check(os.path.exists(out2), "accepts lowercase iso3")

# 404 -> actionable message, no partial file left behind
try:
    ex.fetch_worldpop_population("ZZZ", dest + "3")
    check(False, "unknown country raises")
except ex.ExposureFetchError as e:
    check("no 2020 population raster" in str(e), f"unknown country: {str(e)[:52]}…")
    check(not os.path.exists(dest + "3") and not os.path.exists(dest + "3.part"),
          "no partial file left after a failure")

# host down -> actionable message
ex.WORLDPOP_BASE = "http://127.0.0.1:9/"
try:
    ex.fetch_worldpop_population("NGA", dest + "4")
    check(False, "unreachable host raises")
except ex.ExposureFetchError as e:
    check("Upload an exposure raster instead" in str(e), "unreachable host suggests uploading")

srv.shutdown()
print("\nFAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
