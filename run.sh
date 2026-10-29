#!/usr/bin/env bash
# Start the standalone YuE2 ABC editor.
#
#   ./run.sh                 # http://127.0.0.1:8188
#   ./run.sh --port 9000
#   ./run.sh --no-browser
#
# Creates .venv on first run, rebuilds it if its Python disappears, and
# installs requirements.txt.
set -euo pipefail

cd "$(dirname "$0")"

# A venv keeps a symlink to the interpreter it was built with. Upgrading or
# removing that Python (common with Homebrew) leaves .venv/bin/python dangling,
# so check that it actually runs and rebuild the venv if it does not.
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "" >/dev/null 2>&1; then
    if [ -d .venv ]; then
        echo "Rebuilding virtual environment (its Python is no longer available)…"
        rm -rf .venv
    else
        echo "Creating virtual environment…"
    fi
    python3 -m venv .venv
fi

# requirements.txt currently only holds the test dependency; keep it optional
# so the editor still starts if the install fails (e.g. no network).
if [ -f requirements.txt ]; then
    .venv/bin/python -m pip install -q -r requirements.txt || \
        echo "Note: could not install requirements.txt (the editor runs without it)."
fi

exec .venv/bin/python server.py "$@"
