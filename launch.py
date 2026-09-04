"""
Double-click launcher for the Flood Risk Mapping Platform.

Sets everything up on first run (virtualenv, dependencies, sample data), starts
the server, and opens your browser. No commands to type.

Run it by double-clicking Start-Flood-Tool.command (Mac) or
Start-Flood-Tool.bat (Windows), or with:  python3 launch.py
"""
import os
import socket
import subprocess
import sys
import time
import venv
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
PORT = int(os.environ.get("PORT", "8000"))
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}"


def say(msg=""):
    print(msg, flush=True)


def hr():
    say("-" * 62)


def venv_python():
    p = os.path.join(VENV, "Scripts", "python.exe")   # Windows
    return p if os.path.exists(p) else os.path.join(VENV, "bin", "python")


def port_in_use():
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, PORT)) == 0


def wait_for_server(proc, timeout=180):
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return False
        if port_in_use():
            return True
        time.sleep(0.5)
    return False


def pause_and_exit(code):
    say()
    try:
        input("Press Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass
    sys.exit(code)


def main():
    hr()
    say("  Flood Risk Mapping Platform")
    hr()

    if sys.version_info < (3, 9):
        say(f"\n  This needs Python 3.9 or newer (you have {sys.version.split()[0]}).")
        say("  Install the latest from https://www.python.org/downloads/")
        pause_and_exit(1)

    # Already running? Just open it.
    if port_in_use():
        say(f"\n  Already running. Opening {URL}")
        webbrowser.open(URL)
        pause_and_exit(0)

    if not os.path.exists(venv_python()):
        say("\n  First run: setting up (this takes a minute, only happens once)")
        say("  -> Creating a private Python environment")
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)

    py = venv_python()
    say("  -> Installing components")
    r = subprocess.run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
                       capture_output=True, text=True)
    r = subprocess.run([py, "-m", "pip", "install", "--quiet", "-r",
                        os.path.join(HERE, "requirements.txt")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        say("\n  Setup failed while installing components.\n")
        say((r.stderr or r.stdout or "").strip()[-1500:])
        say("\n  If you are on a work network, a proxy or firewall may be blocking")
        say("  the download. Otherwise send the message above to whoever set this up.")
        pause_and_exit(1)

    sample = os.path.join(HERE, "tests", "sample_data", "population.tif")
    if not os.path.exists(sample):
        say("  -> Preparing example data you can practise with")
        subprocess.run([py, os.path.join(HERE, "tests", "make_sample_data.py")],
                       capture_output=True, text=True)

    say("  -> Starting")
    server = subprocess.Popen(
        [py, "-m", "uvicorn", "main:app", "--app-dir", os.path.join(HERE, "app"),
         "--host", HOST, "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    if not wait_for_server(server):
        say("\n  The tool did not start.\n")
        try:
            say((server.stderr.read() or "").strip()[-1500:])
        except Exception:
            pass
        pause_and_exit(1)

    hr()
    say(f"  Ready. Your browser should open at:  {URL}")
    say()
    say("  To practise first, upload the example files from the folder")
    say("  tests/sample_data:")
    say("     1. Boundaries -> boundaries.zip")
    say("     2. Hazard     -> the eight 1in....tif files (select them all)")
    say("     3. Exposure   -> population.tif  (or type a country code)")
    say()
    say("  KEEP THIS WINDOW OPEN while you work.")
    say("  Close it (or press Ctrl+C) when you are finished.")
    hr()
    webbrowser.open(URL)

    try:
        server.wait()
    except KeyboardInterrupt:
        say("\n  Shutting down...")
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    say("  Stopped.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say(f"\n  Unexpected problem: {e}")
        pause_and_exit(1)
