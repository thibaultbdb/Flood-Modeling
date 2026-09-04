#!/usr/bin/env bash
# Start the Flood Risk Mapping Platform.
#   ./run.sh            -> http://127.0.0.1:8000
#   PORT=9000 ./run.sh  -> http://127.0.0.1:9000
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

# Prefer the quickstart virtualenv if one exists, so the server works whether or
# not the venv is activated.
PY="python3"
[ -x .venv/bin/python ] && PY=".venv/bin/python"
[ -x .venv/Scripts/python.exe ] && PY=".venv/Scripts/python.exe"

echo "Flood Risk Mapping Platform -> http://${HOST}:${PORT}"
exec "$PY" -m uvicorn main:app --app-dir app --host "$HOST" --port "$PORT"
