#!/usr/bin/env bash
# Mac: double-click this file to start the Flood Risk Mapping Platform.
cd "$(dirname "$0")"
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then exec "$PY" launch.py; fi
done
cat <<'MSG'

  Python is not installed on this Mac.

  1. Go to  https://www.python.org/downloads/
  2. Download and install the latest version (just click through).
  3. Then double-click this file again.

MSG
open "https://www.python.org/downloads/" 2>/dev/null
read -r -p "Press Enter to close..."
