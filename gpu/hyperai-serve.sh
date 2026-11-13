#!/usr/bin/env bash
# hyperai-serve.sh — runs ON THE GPU WORKSPACE (HyperAI Jupyter workspace, RTX 5090 32 GB), never on the VPS.
#
# Starts vLLM with an OpenAI-compatible API on 127.0.0.1 only. The VPS reaches it through the SSH
# tunnel it opens itself (`sudo vac gpu connect`), so the port is never public and the workspace holds
# no credential for the VPS, GitHub or production. Everything persistent lives in /hyperai/home.
#
#   export VLLM_API_KEY=...        # printed by `sudo vac gpu init` on the VPS
#   export MODEL_REPO=...          # a 4-bit build of the chosen model (see docs/GPU-AND-MODELS.md)
#   bash hyperai-serve.sh
#
# Safety net against forgotten GPUs: after IDLE_MINUTES without a single request, vLLM is stopped;
# HyperAI's own idle shutdown (CPU < 2 %, page closed, default 30 min) then stops the billing.
# Still: stop the workspace yourself in the HyperAI console when you finish.
set -euo pipefail

# Settings saved once in /hyperai/home/vellur.env (persists across restarts), e.g.:
#   VLLM_API_KEY='vllm-…'
#   MODEL_REPO='RedHatAI/Qwen3.8-27B-INT4'
# Then starting is just:  bash /hyperai/home/hyperai-serve.sh
[ -f /hyperai/home/vellur.env ] && { set -a; . /hyperai/home/vellur.env; set +a; }

: "${VLLM_API_KEY:?set VLLM_API_KEY (from: sudo vac gpu init)}"
: "${MODEL_REPO:?set MODEL_REPO, e.g. a Qwen3.8-27B NVFP4/AWQ build verified on Hugging Face}"
HOME_DIR="${HOME_DIR:-/hyperai/home}"
VLLM_VERSION="${VLLM_VERSION:-}"                 # pin it once a version is confirmed to support the model
SERVED_NAME="${SERVED_NAME:-vellur-coder}"
# Defaults follow the GPU: >= 30 GB VRAM -> 128k context; smaller cards (RTX 3090, 24 GB) -> 32k.
# FP8 KV cache only on Ada/Hopper/Blackwell (compute capability >= 8.9); older cards use "auto".
VRAM_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' .')
if [ "${VRAM_MIB:-0}" -ge 30000 ]; then DEF_LEN=131072; else DEF_LEN=32768; fi
if [ "${CC:-0}" -ge 89 ]; then DEF_KV=fp8; else DEF_KV=auto; fi
MAX_MODEL_LEN="${MAX_MODEL_LEN:-$DEF_LEN}"
GPU_MEM="${GPU_MEM:-0.92}"
KV_CACHE_DTYPE="${KV_CACHE_DTYPE:-$DEF_KV}"
TOOL_PARSER="${TOOL_PARSER:-qwen3_coder}"       # Qwen3-Coder / Qwen3.5+ family — verify on the model card
REASONING_PARSER="${REASONING_PARSER:-qwen3}"   # empty to disable
IDLE_MINUTES="${IDLE_MINUTES:-20}"
PORT="${PORT:-8000}"
EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"               # e.g. --speculative-config '{"method":"qwen3_next_mtp","num_speculative_tokens":2}'

VENV="$HOME_DIR/vllm-venv"
MODEL_DIR="$HOME_DIR/models/$(echo "$MODEL_REPO" | tr '/' '__')"

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
echo "settings: context $MAX_MODEL_LEN tokens, KV cache $KV_CACHE_DTYPE"
# Prefer the vLLM that the HyperAI "VLLM" image already ships (e.g. vllm-0.27.1-2404); otherwise
# install one into a venv under /hyperai/home so it survives restarts.
if [ -z "$VLLM_VERSION" ] && command -v vllm >/dev/null 2>&1; then
	VLLM_BIN="$(command -v vllm)"; PY_BIN="$(dirname "$VLLM_BIN")/python3"; [ -x "$PY_BIN" ] || PY_BIN=python3
	"$PY_BIN" -c 'import huggingface_hub' 2>/dev/null || "$PY_BIN" -m pip install -q "huggingface_hub[cli]" || uv pip install -q --system "huggingface_hub[cli]"
	HF_BIN="$(command -v hf || echo "$(dirname "$VLLM_BIN")/hf")"
else
	if [ ! -x "$VENV/bin/vllm" ]; then
		echo "Installing vLLM ${VLLM_VERSION:-(latest)} into $VENV (persists in /hyperai/home) …"
		python3 -m venv "$VENV"
		"$VENV/bin/pip" install -q --upgrade pip
		"$VENV/bin/pip" install -q "vllm${VLLM_VERSION:+==$VLLM_VERSION}" "huggingface_hub[cli]"
	fi
	VLLM_BIN="$VENV/bin/vllm"; PY_BIN="$VENV/bin/python"; HF_BIN="$VENV/bin/hf"
fi
echo "vLLM $("$PY_BIN" -c 'import vllm; print(vllm.__version__)')  ($VLLM_BIN)"

# Always run: it resumes an interrupted download and returns in seconds when everything is present.
# Slow? Ctrl+C, then `proxy-on` (HyperAI's accelerator) or `export HF_ENDPOINT=https://hf-mirror.com`, and run again.
echo "Downloading/checking $MODEL_REPO → $MODEL_DIR (counts against HyperAI storage) …"
"$HF_BIN" download "$MODEL_REPO" --local-dir "$MODEL_DIR"
du -sh "$MODEL_DIR"

ARGS=(serve "$MODEL_DIR"
	--host 127.0.0.1 --port "$PORT"
	--api-key "$VLLM_API_KEY"
	--served-model-name "$SERVED_NAME"
	--max-model-len "$MAX_MODEL_LEN"
	--gpu-memory-utilization "$GPU_MEM"
	--kv-cache-dtype "$KV_CACHE_DTYPE"
	--enable-prefix-caching
	--enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER")
[ -n "$REASONING_PARSER" ] && ARGS+=(--reasoning-parser "$REASONING_PARSER")
# shellcheck disable=SC2206
[ -n "$EXTRA_ARGS" ] && ARGS+=($EXTRA_ARGS)

pkill -f "vllm serve" 2>/dev/null && sleep 5 || true
echo "Starting: vllm ${ARGS[*]/$VLLM_API_KEY/***}"
"$VLLM_BIN" "${ARGS[@]}" &
VLLM_PID=$!
trap 'kill $VLLM_PID 2>/dev/null || true' EXIT INT TERM

# Idle watchdog: counts finished requests from vLLM's /metrics.
last="" idle=0
until curl -fs -m 3 -o /dev/null -H "Authorization: Bearer $VLLM_API_KEY" "http://127.0.0.1:$PORT/v1/models"; do
	kill -0 $VLLM_PID 2>/dev/null || { echo "vLLM exited during start-up."; exit 1; }
	sleep 5
done
echo "READY: OpenAI API on 127.0.0.1:$PORT (model '$SERVED_NAME'). On the VPS: sudo vac gpu connect <ssh-host> <ssh-port>"
while kill -0 $VLLM_PID 2>/dev/null; do
	sleep 60
	now=$(curl -fs -m 5 "http://127.0.0.1:$PORT/metrics" | awk '/^vllm:request_success_total/ {s+=$NF} END {print s+0}')
	if [ "$now" = "$last" ]; then idle=$((idle + 1)); else idle=0; last=$now; fi
	if [ "$IDLE_MINUTES" -gt 0 ] && [ "$idle" -ge "$IDLE_MINUTES" ]; then
		echo "No requests for $IDLE_MINUTES min: stopping vLLM so the workspace can auto-shut down. Stop it in the HyperAI console to be sure."
		kill $VLLM_PID; wait $VLLM_PID || true; exit 0
	fi
done
