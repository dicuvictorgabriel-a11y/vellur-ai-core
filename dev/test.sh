#!/usr/bin/env bash
# Run the whole automated test suite of VELLUR AI Core (the same command CI runs).
set -euo pipefail
cd "$(dirname "$0")/.."
. dev/env.sh
cd autopilot && exec "../$VENV/bin/python" -m pytest -q "$@"
