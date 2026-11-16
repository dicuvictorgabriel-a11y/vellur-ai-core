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

## API-first architecture (2026-11-14, docs/ARCHITECTURE.md)

Inference goes through `autopilot/app/router.py`: public names (vellur-coder/chat/max) → ordered targets
(`provider:model` OpenAI-compatible APIs, or `gpu`). Keys are write-only on the server. Usage and cost are recorded per
request; paid targets obey the monthly budget; paid fallback is opt-in. HyperAI is optional, not the foundation.
Workspace (chat with history, models, costs, agent) lives in `workspace.py` + `static/dashboard.html`, all behind the
existing login. Coding agent: `agent.py` (controller, keeps the GitHub token) + `agent/` (isolated OpenCode runner).
Agent design, event log, outcomes and the failed-test investigation: `docs/AGENT.md`. A task's `state` is the process
state; `outcome` is the judged result. Never report `completed` from an exit code alone.
**`app/auth.py` and the login/change-password forms are frozen by the owner** (tests/test_auth_frozen.py): do not
change them without explicit approval. No automatic request-type routing (no measured benefit yet).

## GPU Autopilot (autopilot/, docs/AUTOPILOT.md)

FastAPI + SQLite controller in its own container (profile `autopilot`, 256 MB). LiteLLM's `vellur-coder` points to its
inference proxy `gpu-backend:8000`; it ensures a GPU (start/attach, hold the request), streams, counts activity, and a
watchdog handles idle / session limit / budget / unverified stops / orphans / billing sync. Providers: HyperAI
**assisted** (no documented start/stop API — do not invent one), Vast.ai automated (endpoints copied from the official
`vastai` SDK 1.11.0; untested with a real account), mock for tests. DRY RUN unless `AUTOPILOT_LIVE=1`
(`sudo vac autopilot live on`). Dashboard: public read-only view (page + /autopilot/api/public/*, redacted); controls behind an in-page login
(app/auth.py: PBKDF2 hash, cookie sessions, forced change of the temporary password, throttling) + CSRF header.
The X-Vellur-Admin secret header is for local `vac` only; Caddy strips it from internet requests. No basic auth.
Tests: `cd autopilot && pytest -q` (no network: guarded). Never let tests reach a real provider.

## Open work (priority order)

1. Owner runs `deploy-from-pc.ps1` (installs Autopilot in dry run; prints the dashboard password once), rotates the
   vLLM key, creates `vellur.env` on HyperAI. First real Autopilot test: attach a HyperAI workspace from the dashboard.
2. Test OpenCode end to end with the real model (tool calling: create/edit a file).
3. Check whether the `bayes` CLI or an official HyperAI API can start/stop workspaces; if yes, make
   `providers/hyperai.py` automated. Vast.ai: first live rental test once the owner adds a key and approves.
4. Verify `gpu/autostart.sh` (`/hyperai/home/dependencies.sh`) on a workspace.
5. Decide Open WebUI on the 4 GB VPS (`sudo vac ui enable` checks RAM) vs. a lighter chat UI.
6. Add CI for this folder (shellcheck, `bash -n`, compose config, Caddy validate on 2.6/2.8/latest, stub e2e).

## Working on VELLUR from inside VELLUR (self-development)

- You work on a copy of this repository in an isolated workspace. The live server changes only when the owner
  publishes to `main` and then approves that exact version in the dashboard (Activity → VELLUR version); the server
  installs it only if the `tests` workflow passed for that commit, and restores the previous release on failure.
  You cannot install anything and must not try to.
- Live preview: `.vellur/preview.json` starts `dev/preview.sh`, which runs the real dashboard from your checkout with
  an empty demo database (`dev/preview_server.py`, never installed on the server). Edit
  `autopilot/app/static/dashboard.html` and the preview shows it after a reload. Python changes need the preview
  process restarted (the owner's Reload button does not restart it; say so in your report).
- Tests: `bash dev/test.sh` (the whole suite, the same as CI). Run it before reporting.
- Frozen: `autopilot/app/auth.py`, the login and password-change markup and their script block in `dashboard.html`
  (`tests/test_auth_frozen.py` fails if they change). Keep `localStorage` out of the dashboard (a test checks it).
- The dashboard is one static file without a build step; keep it that way.

## NOVA (2026-11-16, docs/NOVA.md)

The product's visible name is the temporary codename **NOVA** (`BRAND` constant in `dashboard.html`); technical
identifiers stay `vellur` (domain, paths, cookies, model ids, headers, repository). The conversation view is the
workspace: modes Discover / Build / Review / Ship over one linked task (`#modeBar`, `#stage`). Release status comes
from `Release.delivery()` (`GET /autopilot/api/agent/tasks/{id}/delivery`): task record, GitHub and the installer's
report; unconfirmed stages are `unknown`, never done. `docs/NOVA.md` lists implemented vs proposed (event ledger,
cost ledger, router modes, Knowledge are NOT built). Browser journey: `node dev/workspace-smoke.cjs`.

## Task recovery (2026-11-16, docs/RECOVERY.md)

A controller restart no longer kills agent tasks: `AgentManager.recover()` re-attaches to a run that is still going,
collects one that ended, or resumes the same OpenCode session automatically (max 2, budget/workspace checked);
publications in progress are paused for the owner. Model tokens persist as hashes; the runner saves the session id at
once. Restart diagnostics: `app/health.py` (boot records clean/unclean + memory), `/autopilot/api/diagnostics/restarts`.
Failure injection with the real runner process: `tests/test_recovery_integration.py`. Root cause of the 2026-11-16
incident is NOT confirmed yet (server logs needed).

## Design system (owner decision 2026-11-16, docs/CARBON.md)

Use **IBM Carbon** (github.com/carbon-design-system/carbon) as the interaction/layout reference from now on, adapted to
the single static dashboard file (no React, no build step, no copied code). The UI Shell is implemented: phone menu
button + overlay side nav, desktop rail collapse (`#navOpen`, `#bnMenu`, `#railToggle`, shell script at the end of
`dashboard.html`). Earlier pattern notes: docs/ASTRYX.md.
