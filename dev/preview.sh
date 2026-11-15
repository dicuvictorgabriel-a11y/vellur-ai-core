#!/usr/bin/env bash
# Start the staging preview of the dashboard on $PORT (see dev/preview_server.py). Used by VELLUR's Live Preview
# through .vellur/preview.json and usable by hand. Creates a private Python environment on first use.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VELLUR_DEV_VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
	python3 -m venv "$VENV"
	"$VENV/bin/pip" install -q --disable-pip-version-check -r autopilot/requirements-dev.txt
fi
exec "$VENV/bin/python" dev/preview_server.py
