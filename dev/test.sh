#!/usr/bin/env bash
# Run the whole automated test suite of VELLUR AI Core (the same command CI runs).
set -euo pipefail
cd "$(dirname "$0")/.."
. dev/env.sh
# Inside the agent container /tmp is a 512 MB tmpfs shared by every task. When TMPDIR is not already a per-task
# directory, move pytest's temp tree to the work volume and prune only our own stale runs (never unrelated files).
if [ -z "${TMPDIR:-}" ] || [ "$TMPDIR" = "/tmp" ] || [ "$TMPDIR" = "/var/tmp" ]; then
	if [ -n "${AGENT_WORK:-}" ] && [ -d "$AGENT_WORK" ] && [ -w "$AGENT_WORK" ]; then
		TMPDIR="$AGENT_WORK/.tmp/pytest"
		mkdir -p "$TMPDIR"
		find "$TMPDIR" -mindepth 1 -maxdepth 1 -type d -name 'pytest-*' -mtime +7 -exec rm -rf {} + 2>/dev/null || true
		export TMPDIR
	fi
fi
cd autopilot && exec "../$VENV/bin/python" -m pytest -q "$@"
