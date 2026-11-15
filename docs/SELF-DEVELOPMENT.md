# VELLUR develops VELLUR — architecture, release process, recovery (15.11.2026)

## The idea in one picture

```
 you (browser, logged in)
   │  "Improve VELLUR itself: …"                       approve version abc1234
   ▼                                                           │
 controller (container, no root) ──► agent container           ▼
   · keeps GitHub token + keys        · copy of the repository          release request (database)
   · records events, costs            · OpenCode edits, runs tests                │
   · serves the sandboxed preview ◄── · staging dashboard (dev/preview_server.py) │
   │  "Publish to main" (your click)                                              │
   ▼                                                                              ▼
 GitHub main ──► tests workflow (CI)                 vac-autoupdate (host, root, systemd timer)
                                                      · installs ONLY the approved commit, ONLY if CI passed
                                                      · vac verify → on failure re-installs the previous release
```

Nothing in the left column can change the running server. The agent works on a copy; the preview is a separate
throwaway instance with an empty demo database; the only path to production is: your Publish click → CI → your
Install click → the host installer.

## What protects production

| Risk | Protection |
|---|---|
| The agent breaks the running app while experimenting | It edits a copy in its own container; it has no access to the server's files, Docker socket or SSH. |
| A previewed page steals the session or calls the API | Preview is served with `Content-Security-Policy: sandbox` in a sandboxed frame (origin `null`); link token created only for a logged-in owner, 12 h. |
| A pushed commit installs itself | Nothing is installed because it was pushed. `vac-autoupdate` installs only the commit recorded as approved by the owner (dashboard, behind login) or typed on the server. |
| An untested version is installed | The installer refuses a commit whose `tests` workflow did not pass (failed, missing or still running). |
| A broken version passes the tests | After installing: `vac verify`; on failure the previous release (kept on disk) is re-installed and the commit is never retried. |
| The dashboard itself is broken, so nothing can be approved | Recovery does not need the dashboard: `sudo vac autoupdate rollback` on the server, or the manual deploy from the PC. Every manual install is also kept as a rollback target. |
| Updating kills a running agent task | The installer waits while a task runs. |
| Runaway cost | Per-run limits (minutes, dollars), monthly budget, server-side ceilings in `.env` the dashboard cannot exceed, one task at a time, provider-side key limits recommended. |
| Secrets | GitHub token and provider keys stay in the controller's database (write-only in the UI); the agent gets a short-lived inference token per run; the VPS has no credentials stored in GitHub. |

## Working on VELLUR from VELLUR

1. Agent → New task → "Improve VELLUR itself" (or pick `vellur-ai-core`). Describe the change.
2. The agent works in a persistent workspace; the Live Preview shows the real dashboard from that workspace
   (`.vellur/preview.json` → `dev/preview.sh`), signed in as a demo owner with demo data.
3. Every run ends with a checkpoint. Screenshots and AI visual inspection are separate, optional choices for each run.
   "Versions" lists checkpoints; any can be restored, and a restore can be undone.
4. Happy with it: "Publish to main" in the report. GitHub runs the tests.
5. Activity → VELLUR version → "Install version …" (enabled when the tests passed). The server installs it within
   two minutes and reports the result there. "Return to the previous version" undoes it.

## Server commands (only for setup and emergencies)

```
sudo vac autoupdate on            # once: start checking for approved releases (installs nothing by itself)
sudo vac autoupdate status        # installed version, previous version, last result
sudo vac autoupdate install latest    # approve + install from the server (needs passed tests; --force to skip)
sudo vac autoupdate rollback      # return to the previous release; works when the dashboard is down
sudo vac autoupdate off
```

## Recovery if VELLUR breaks

1. Dashboard still opens: Activity → VELLUR version → "Return to the previous version".
2. Dashboard does not open: on the server, `sudo vac autoupdate rollback`.
3. That fails too: from the PC, check out the last good commit and run `deploy-from-pc.ps1` (the original manual
   installer; it never depended on VELLUR's agent or updater).
4. Data: `sudo vac backup` / `sudo vac restore` (unchanged). An install never deletes the database volume.

## Verification status (honest)

| Item | Status |
|---|---|
| Unit and API tests (98) locally and in GitHub Actions on the pushed commit | TESTED |
| Self-development acceptance test in an isolated staging copy inside the build sandbox: real repository downloaded from GitHub, real OpenCode, real runner and controller, real browser; a scripted server instead of a model | VERIFIED END-TO-END (staging, scripted model) |
| Release installer logic: approval, CI gate, wait, install, automatic restore, rollback with the dashboard down (fake installer and fake GitHub) | TESTED |
| Anything on the VPS: installation, systemd timer, agent image build with the browser, real model quality, real publish to GitHub from the dashboard | NOT VERIFIED (no VPS access from the build session; no model provider key) |

## Limits to know

- The preview of VELLUR restarts only when its process is restarted; edits to `dashboard.html` show after a reload,
  Python changes need the preview process restarted (a follow-up run does it when it changes the preview command).
- The model matters: the acceptance test used scripted steps. A weak model may not manage a multi-step change.
- Inspecting screenshots needs a model with image input (switch in Agent → GitHub connection) — not verified with a
  real model.
- One task at a time; the agent container has 2 GB of memory.

## Path to a multi-user product (not built)

Kept compatible on purpose, nothing more:
- Every task already has its own workspace folder, session, inference token and cost tag (`task:<id>`): the unit a
  tenant would own. A `tenant_id` column on `tasks`, `conversations`, `usage` and per-tenant settings is the first step.
- The runner speaks HTTP with a shared secret and holds no credentials, so it can become a pool of workers
  (one container or micro-VM per run) behind a queue without changing the controller's API.
- Preview links are capability tokens per task; per-tenant subdomains would replace the path prefix.
- Dependencies are permissively licensed: OpenCode (MIT), FastAPI/Uvicorn/httpx (BSD/MIT), LiteLLM (MIT, without its
  enterprise folder), Playwright (Apache-2.0), Chromium (BSD). Model providers' terms are per provider.
- Before any public use: real sandboxing for untrusted users (gVisor/Firecracker), per-user auth, rate limits, and
  network egress rules for the agent container.
