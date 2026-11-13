#!/usr/bin/env bash
# autostart.sh — EXPERIMENTAL, untested on HyperAI. Starts the model automatically when the workspace starts.
#
# Setup once:
#   1. In the HyperAI container form → Environment variables:
#        VLLM_API_KEY = <key from `sudo vac gpu init`>   (tick "Secret")
#        MODEL_REPO   = RedHatAI/Qwen3.8-27B-INT4
#        (RTX 3090 only: MAX_MODEL_LEN = 32768 and KV_CACHE_DTYPE = auto)
#   2. Copy this file to /hyperai/home/dependencies.sh (HyperAI runs it at every container start)
#      next to /hyperai/home/hyperai-serve.sh.
# Log: /hyperai/home/vllm.log  — "READY" appears there when the model answers.
cd /hyperai/home || exit 0
if [ -n "${VLLM_API_KEY:-}" ] && [ -n "${MODEL_REPO:-}" ] && ! pgrep -f "vllm serve" >/dev/null; then
	nohup bash /hyperai/home/hyperai-serve.sh > /hyperai/home/vllm.log 2>&1 &
	echo "VELLUR: model starting in background, log: /hyperai/home/vllm.log"
fi
exit 0
