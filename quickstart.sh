#!/usr/bin/env bash
# One-command setup: creates a virtualenv, installs dependencies, generates a
# synthetic sample dataset, and starts the server.
#
#   ./quickstart.sh
#
# Re-running is cheap: existing venv and sample data are reused.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Error: $PY not found. Install Python 3.9+ and re-run." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "==> Creating virtualenv (.venv)"
  "$PY" -m venv .venv
fi
VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=".venv/Scripts/python.exe"   # Windows / Git Bash

echo "==> Installing dependencies"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt

if [ ! -f tests/sample_data/population.tif ]; then
  echo "==> Generating synthetic sample dataset (tests/sample_data/)"
  "$VENV_PY" tests/make_sample_data.py
fi

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
cat <<BANNER

  Flood Risk Mapping Platform
  ---------------------------
  Open:  http://${HOST}:${PORT}

  To try it with the sample data, upload from tests/sample_data/ :
    1. Boundaries : boundaries.zip
    2. Hazard     : 1in5.tif 1in10.tif ... 1in1000.tif   (select all eight)
    3. Exposure   : population.tif
  Then press "Run flood risk analysis". Ctrl-C here to stop the server.

BANNER
exec "$VENV_PY" -m uvicorn main:app --app-dir app --host "$HOST" --port "$PORT"
