#!/usr/bin/env bash
# Start the staging preview of the dashboard on $PORT (see dev/preview_server.py). Used by VELLUR's Live Preview
# through .vellur/preview.json and usable by hand. Creates a private Python environment on first use.
set -euo pipefail
cd "$(dirname "$0")/.."
. dev/env.sh
exec "$VENV/bin/python" dev/preview_server.py
