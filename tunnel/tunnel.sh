#!/bin/sh
# Keeps a local forward  0.0.0.0:8000 (inside the vellur-ai-core network only)  →  GPU workspace 127.0.0.1:$GPU_REMOTE_PORT.
# When the GPU workspace is off, it waits and retries every 30 s instead of crash-looping.
: "${GPU_SSH_HOST:?GPU_SSH_HOST not set}" "${GPU_SSH_PORT:?GPU_SSH_PORT not set}"
pid=""
trap '[ -n "$pid" ] && kill "$pid" 2>/dev/null; exit 0' TERM INT
while true; do
	echo "connecting to ${GPU_SSH_USER:-root}@${GPU_SSH_HOST}:${GPU_SSH_PORT} …"
	ssh -N -T \
		-i /run/vellur/gpu_ed25519 \
		-o IdentitiesOnly=yes -o BatchMode=yes \
		-o UserKnownHostsFile=/run/vellur/gpu_known_hosts -o StrictHostKeyChecking=yes \
		-o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
		-o ConnectTimeout=20 \
		-L "0.0.0.0:8000:127.0.0.1:${GPU_REMOTE_PORT:-8000}" \
		-p "${GPU_SSH_PORT}" "${GPU_SSH_USER:-root}@${GPU_SSH_HOST}" &
	pid=$!; wait "$pid"; rc=$?; pid=""
	echo "tunnel down (exit $rc); GPU workspace stopped or unreachable. Retrying in 30 s."
	sleep 30 & pid=$!; wait "$pid"; pid=""
done
