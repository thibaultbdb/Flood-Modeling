#!/usr/bin/env bash
# Start the Flood Risk Mapping Platform.
#   ./run.sh            -> http://127.0.0.1:8000
#   PORT=9000 ./run.sh  -> http://127.0.0.1:9000
set -euo pipefail
cd "$(dirname "$0")/app"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
echo "Flood Risk Mapping Platform -> http://${HOST}:${PORT}"
exec python3 -m uvicorn main:app --host "$HOST" --port "$PORT"
