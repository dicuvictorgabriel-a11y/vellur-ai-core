# Handover to Codex — VELLUR AI Core (2026-11-14)

Repo: `dicuvictorgabriel-a11y/vellur-ai-core`, branch `main`. Talk to the owner in **Romanian, simply** (product owner,
not a developer). Read first: `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/AUTOPILOT.md`, `README.md`.

## Hard rules
- **Login is frozen:** `autopilot/app/auth.py`, the login/change-password forms and their JS in
  `autopilot/app/static/dashboard.html`, and the `/autopilot/api/auth/*` endpoints. `tests/test_auth_frozen.py`
  checks the hash. Changing them needs the owner's explicit approval.
- No spending, no paid GPU, no `AUTOPILOT_LIVE=1`, no subscriptions. Propose; the owner decides.
- No secrets in git or chat. Provider keys and the GitHub token are entered by the owner in the dashboard.
- You have no access to the VPS (`ro-prod-01`) or providers: give exact commands and read the owner's output.
- Never claim it works with a real model or provider unless the owner's output shows it.

## State
- **Deployed on the VPS:** the version before the API-first reset (GPU Autopilot + in-page login). The owner has
  not yet run `deploy-from-pc.ps1` for the current `main`.
- **On `main`, sandbox-tested, not on the VPS:** API-first model router, workspace (chat history, models, costs,
  agent), isolated OpenCode agent container.
- **Never tested for real:** any model provider, GitHub publishing, Vast.ai, model quality.

## Architecture (short)
`/v1` (LiteLLM, client keys via Caddy) and the dashboard both call the router in the Autopilot container
(`autopilot/app/router.py`, internal `gpu-backend:8000`). Public names `vellur-coder|chat|max` → ordered targets
`provider:model-id` (OpenAI-compatible) or `gpu`. Keys write-only on the server; usage + cost per request in SQLite
(`usage` table; `provider` = reported, `estimate` = tokens × price). Paid targets stop at the monthly budget; paid
fallback is opt-in. Agent: `autopilot/app/agent.py` (controller keeps the GitHub token, downloads the tarball,
publishes branch + draft PR only on click) + `agent/runner.py` (isolated container, network `vellur-ai-agents`, no
secrets, short-lived inference token). GPU Autopilot (`lifecycle.py`, `providers/`) is an optional backend.

## Tests
`cd autopilot && pip install -r requirements-dev.txt && pytest -q` → 85 pass, network-guarded. The sandbox recipe
(mock Caddy, sshd GPU stand-in, `tests/openai_stub.py --agent-script`) is in `AGENTS.md`. `vac verify` on the VPS.

## Next work (priority)
1. Owner deploys `main`; adds an OpenRouter key; routes `vellur-coder` to a `:free` model; real chat test.
2. First real agent task on COMMON (`dicuvictorgabriel-a11y/bloom-vellur-studio`, folder `common/`) with a
   fine-grained GitHub token; check log, diff, draft PR.
3. Choose models by cost per successful task (record task outcome + cost; compare 2–3 models on the same tasks).
4. Only then: automatic routing by request type, if it measurably helps.
5. Image input in chat; browser automation; Blender later.

## Known caveats
- GitHub Actions CI for this repo does not exist yet (allowed: repo is standalone now).
- Agent image ~2.8 GB; installer enables it only with ≥ 2.5 GB free RAM and ≥ 8 GB disk.
- OpenCode Console had a key breach in Oct 2026: use only the local CLI, never Console.
- Model names/prices in the docs come from public sources (Nov 2026); use the provider's live model list.
