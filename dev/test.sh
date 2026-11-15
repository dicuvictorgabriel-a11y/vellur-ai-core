#!/usr/bin/env bash
# Run the whole automated test suite of VELLUR AI Core (the same command CI runs).
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VELLUR_DEV_VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
	python3 -m venv "$VENV"
	"$VENV/bin/pip" install -q --disable-pip-version-check -r autopilot/requirements-dev.txt
fi
cd autopilot && exec "../$VENV/bin/python" -m pytest -q "$@"
