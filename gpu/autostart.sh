#!/usr/bin/env bash
# autostart.sh — EXPERIMENTAL (not yet confirmed on HyperAI workspaces). Starts the model when the
# workspace starts. Copy it to /hyperai/home/dependencies.sh next to hyperai-serve.sh and vellur.env.
# Log: /hyperai/home/vllm.log  ("READY" appears there when the model answers).
cd /hyperai/home || exit 0
if { [ -f /hyperai/home/vellur.env ] || [ -n "${VLLM_API_KEY:-}" ]; } && ! pgrep -f "vllm serve" >/dev/null; then
	nohup bash /hyperai/home/hyperai-serve.sh > /hyperai/home/vllm.log 2>&1 &
	echo "VELLUR: model starting in background, log: /hyperai/home/vllm.log"
fi
exit 0
