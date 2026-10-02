#!/usr/bin/env bash
# Start the standalone YuE2 ABC editor.
#
#   ./run.sh                 # http://127.0.0.1:8188
#   ./run.sh --port 9000
#   ./run.sh --no-browser
#
# Creates .venv on first run and installs requirements.txt.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating virtual environment…"
    python3 -m venv .venv
fi

# requirements.txt currently only holds the test dependency; keep it optional
# so the editor still starts if the install fails (e.g. no network).
if [ -f requirements.txt ]; then
    .venv/bin/python -m pip install -q -r requirements.txt || \
        echo "Note: could not install requirements.txt (the editor runs without it)."
fi

exec .venv/bin/python server.py "$@"
