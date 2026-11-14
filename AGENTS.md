# VELLUR AI CORE — instructions for agents (Codex, Claude, others)

Talk to the owner in **Romanian, simply**: they are the product owner, not a developer.

## What it is

A private AI coding stack. The existing production VPS (`ro-prod-01`, Ubuntu, 2 vCPU, 4 GB RAM, also hosts other
VELLUR sites behind a system Caddy) is the control plane; inference runs on a rented GPU workspace (HyperAI) that is
switched on only when needed; OpenCode/VS Code are the clients.

```
OpenCode ─HTTPS─► ai.vellurdigital.com/v1 (Caddy: client key → master key) ─► LiteLLM (127.0.0.1:3311)
          ─► gpu-tunnel container ═SSH (opened by the VPS)═► HyperAI workspace: vLLM on 127.0.0.1:8000
```

Read `README.md` (Romanian, owner-facing) and `docs/GPU-AND-MODELS.md` (HyperAI facts, model choice, costs) first.

## Files

- `install.sh` — idempotent installer run on the VPS (discovery, optional swap/Docker with consent, files to
  `/opt/vellur-ai-core`, secrets in `.env` mode 600, start, first client key, publish, verify).
- `bin/vac` — the only admin tool on the VPS (`sudo vac status|verify|keys|expose|unexpose|gpu|ui|backup|restore`).
  It generates `/etc/caddy/vellur-ai-core.caddy` and adds ONE import line to `/etc/caddy/Caddyfile`, always with
  backup → validate → reload → automatic restore on failure.
- `compose.yaml` — `gateway` (LiteLLM, always), `gpu-tunnel` (profile `gpu`), `webui` (Open WebUI, profile `ui`).
- `litellm/` — `config.yaml`, `models.yaml` (active), `models.gpu.yaml` (activated by `vac gpu connect`), templates.
- `tunnel/` — alpine + openssh-client, retries every 30 s instead of crash-looping.
- `gpu/hyperai-serve.sh` — runs ON the GPU workspace: vLLM on 127.0.0.1 only, API key, idle watchdog,
  GPU-aware defaults, settings from `/hyperai/home/vellur.env`. `gpu/autostart.sh` — experimental.
- `www/index.html` — static status page (no admin functions). `opencode/` — client config and a PowerShell test.
- `deploy-from-pc.ps1` — the owner's way to ship this folder to the VPS and run the installer.
- `tests/openai_stub.py` — OpenAI protocol stub that answers "[protocol stub: no model]" (wiring tests only).

## Hard rules

- Never spend money, subscribe, top up, or start paid GPUs. Propose; the owner decides and clicks.
- Never commit secrets (`.env`, `secrets/`, keys, tokens). Never ask the owner to paste secrets in chat.
- Never break or restart other production services on the VPS. Every Caddy change keeps the backup/validate/restore
  pattern; `vac unexpose` must restore the Caddyfile byte for byte.
- No AI agent gets shell access to the production VPS, its Docker socket, or its SSH keys. The GPU workspace gets
  no credential for the VPS, GitHub or production.
- Agents usually have NO access to the VPS or HyperAI: give the owner exact commands, then read their output.
- Do not claim something works on the real server or with a real model unless the owner's output shows it.
- Keep the VPS footprint small: no databases, no local inference, Open WebUI only when RAM allows.

## How to test here (no VPS needed)

Recreate the sandbox used so far: `dockerd` running; a `caddy:2-alpine` container on the host network with a mock
Caddyfile that already serves another site with `basic_auth` (to prove it stays untouched); run `install.sh` with
`VAC_HOME`, `CADDYFILE`, `CADDY_SNIPPET`, `CADDY_GROUP=root`, `CADDY_VALIDATE="docker exec <c> caddy validate …"`,
`CADDY_RELOAD="docker exec <c> caddy reload …"`; simulate the GPU with an sshd container plus `tests/openai_stub.py`
in its network namespace; `vac verify` must pass. Validate Caddy output on 2.6–2.10.

## Known facts (verified)

- Live: `https://ai.vellurdigital.com` (Cloudflare A record, DNS only, 185.4.113.26). The VPS's public IP is NOT on
  its interface (NAT); `vac` therefore also trusts the IPs of sites already in the Caddyfile.
- First real inference worked on 2026-11-13: HyperAI H100, image `vllm-0.27.1-2404`, model
  `RedHatAI/Qwen3.8-27B-INT4` (18 GiB weights), 128k context, FP8 KV cache, tool parser `qwen3_coder`, reasoning
  parser `qwen3`. Model stored in `/hyperai/home/models/` (persists). HyperAI SSH gateway: `ssh.hyper.ai:<port>`,
  the port changes per execution; key auth works.
- HyperAI: `proxy-on` points to a dead host — use `proxy-off` and unset proxy variables. Hugging Face downloads can
  slow down; `HF_ENDPOINT=https://hf-mirror.com` is the fallback. RTX 5090 shows "HyperAI Pro"; GPUs are often
  "FULL LOAD". Idle auto-shutdown: 30 min, only with CPU < 2 % and the JupyterLab tab closed.
- Gateway (LiteLLM v1.108.7-lts, no DB): ~550 MB idle, ~610 MB after requests (limit 1 GB), ~25 s start on 1 CPU.
  Without a DB, only the master key exists; per-client keys are enforced by Caddy (`header_regexp`, one regex).
- Pitfalls already fixed: Caddy `header` matcher takes one value per line (multi-key broke); Caddy < 2.8 needs
  `wrap json` in the log filter; Ubuntu's default awk is mawk (no `{n,m}` intervals); the reasoning model needs
  max_tokens ≥ ~300 or the answer is empty.

## GPU Autopilot (autopilot/, docs/AUTOPILOT.md)

FastAPI + SQLite controller in its own container (profile `autopilot`, 256 MB). LiteLLM's `vellur-coder` points to its
inference proxy `gpu-backend:8000`; it ensures a GPU (start/attach, hold the request), streams, counts activity, and a
watchdog handles idle / session limit / budget / unverified stops / orphans / billing sync. Providers: HyperAI
**assisted** (no documented start/stop API — do not invent one), Vast.ai automated (endpoints copied from the official
`vastai` SDK 1.11.0; untested with a real account), mock for tests. DRY RUN unless `AUTOPILOT_LIVE=1`
(`sudo vac autopilot live on`). Dashboard behind Caddy basic auth + secret header checked server-side + CSRF header.
Tests: `cd autopilot && pytest -q` (60, no network: guarded). Never let tests reach a real provider.

## Open work (priority order)

1. Owner runs `deploy-from-pc.ps1` (installs Autopilot in dry run; prints the dashboard password once), rotates the
   vLLM key, creates `vellur.env` on HyperAI. First real Autopilot test: attach a HyperAI workspace from the dashboard.
2. Test OpenCode end to end with the real model (tool calling: create/edit a file).
3. Check whether the `bayes` CLI or an official HyperAI API can start/stop workspaces; if yes, make
   `providers/hyperai.py` automated. Vast.ai: first live rental test once the owner adds a key and approves.
4. Verify `gpu/autostart.sh` (`/hyperai/home/dependencies.sh`) on a workspace.
5. Decide Open WebUI on the 4 GB VPS (`sudo vac ui enable` checks RAM) vs. a lighter chat UI.
6. Add CI for this folder (shellcheck, `bash -n`, compose config, Caddy validate on 2.6/2.8/latest, stub e2e).
