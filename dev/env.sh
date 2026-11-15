#!/usr/bin/env bash
# Shared by dev/preview.sh and dev/test.sh: one private Python environment per checkout, created once.
# A lock and a "ready" marker keep two scripts that start together from using a half-installed environment.
VENV="${VELLUR_DEV_VENV:-.venv}"
(
	flock 8
	if [ ! -f "$VENV/.ready" ]; then
		python3 -m venv "$VENV"
		"$VENV/bin/pip" install -q --disable-pip-version-check -r autopilot/requirements-dev.txt
		touch "$VENV/.ready"
	fi
) 8> "${TMPDIR:-/tmp}/vellur-venv-$(pwd | cksum | cut -d' ' -f1).lock"
