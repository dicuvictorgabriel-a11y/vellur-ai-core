# NOVA React-only cutover candidate (branch `nova/react-only`, 2026-11-17)

This branch is the prepared, tested **React-only** Mission Control candidate requested by the owner. It makes the
React app the single proposed production frontend and retires the legacy dashboard from the proposed architecture.
**Nothing was deployed, restarted or merged**: the live NOVA instance keeps running exactly as before; adoption
happens only when the owner reviews, merges and approves a release.

## Isolation (evidence)

- Work happens in the isolated VELLUR workspace copy: single-commit repo (`f71e414` "baseline"), **no git remote
  configured** (`git remote -v` empty), no credentials for the production VPS, GitHub or any provider.
- The production server was not contacted: no deploy commands, no SSH, no container control were used.
- The staging preview (`dev/preview_server.py`) runs from this checkout with an empty demo database, dry-run mode,
  a simulated in-memory agent runner and no provider keys. Restarting it (PID change only) is local to the sandbox.
- Builds ran outside the working tree (`/work/t36/tmp/opencode/nb`); no `node_modules` was added to the repo.

## Legacy archive (Phase B)

- Pre-change commit: `f71e414` (`vellur-base`, already present) — the last revision where `/autopilot/` serves the
  legacy dashboard.
- Archive ref: **git tag `legacy-dashboard-archive`** (annotated, → `f71e414`), created before any edit.
- Local checkout verified: `git worktree add /tmp/legacy-archive-verify legacy-dashboard-archive`;
  `autopilot/app/static/dashboard.html` restored, `sha256 89d84b2e743286808f7069578be138cd6516424c863dac0c3d4c9b472ffb9437`
  identical to the working-tree file at that commit; worktree removed afterwards.
- **Remote backup: NOT verified — no git remote exists in this workspace.** Pushing
  `git push <private-remote> legacy-dashboard-archive` is an owner action and a prerequisite for any real cutover.

### Restoring the legacy source

```sh
git checkout legacy-dashboard-archive -- autopilot/app/static/dashboard.html
# or the whole historical tree:
git worktree add /tmp/legacy-restore legacy-dashboard-archive
```

### Operator rollback of a deployed release

The release pipeline itself is the rollback mechanism: in the dashboard Release view request **Rollback**, or on the
VPS run the installer flow with the previous approved release (`sudo vac` workflow / re-run
`deploy-from-pc.ps1`-shipped installer for the previous commit). The installer keeps the previous release and
reverts on failed checks; `bin/vac-autoupdate` refuses an install when the approved commit has no matching
`nova-react-build.tar.gz` asset, which also leaves the previous release in place. A Git ref is not a deployed
rollback artifact: the rollback artifact is the previous release directory on the VPS.

## What the branch changes

1. `autopilot/app/api.py` — `/autopilot/` serves the built React app (`static/react/index.html`); when the bundle
   is missing it answers **503 `react_build_missing`** with operator guidance instead of falling back to legacy.
   New `GET /autopilot/assets/{path}` serves the bundle's hashed assets (the build uses relative URLs, so the same
   bundle works at `/autopilot/` and at the kept alias `/autopilot/react/`). No backend API, auth, SSE or agent
   behavior changed.
2. `autopilot/app/static/dashboard.html` — **deleted** (459,117 bytes, sha256 above). Proven unused on this branch:
   the only remaining references were its own route (removed) and tests that read the file directly.
3. `autopilot/tests/test_dashboard.py` — **deleted**; its 19 tests only exercised the deleted legacy file (script
   parseability, no secrets, no basic auth). The equivalent guarantees for React come from `tsc` + the production
   build + 14 Playwright journeys.
4. `autopilot/tests/test_3d.py` — asserts `dashboard.html` is gone; Precision Studio checks now read the remaining
   `studio3d.html`. The path-traversal test (`/autopilot/3d/%2e%2e/dashboard.html` → 404) is unchanged and still
   passes.
5. `autopilot/app/selfdev.py` — the publication guard's browser invariants (no `localStorage`, no
   `X-Vellur-Admin`) now also scan `frontend/` sources, so the guard does not silently die with the legacy file.
   `autopilot/app/auth.py` and `autopilot/tests/test_auth_frozen.py` are untouched (both are hash-checked and still
   pass — `test_auth_frozen.py` only pins `auth.py`).
6. `frontend/` — stale legacy-era copy replaced ("The classic dashboard remains available…", "Open classic NOVA"
   self-link in ToolsView, login fallback link, noscript line). Honest gap notes kept (see matrix).

Frozen files respected: `app/auth.py` untouched; `test_auth_frozen.py` untouched; `bin/vac-autoupdate` untouched;
no Caddy change.


## React parity matrix (verified in this run)

Evidence: 14 Playwright journeys + 361 backend tests + live preview route checks (see Tests). Legacy capabilities
without a React equivalent are listed under **Deferred (disclosed)** — they are NOT silently dropped: the branch
keeps the backend APIs that power them.

| Legacy capability | React status | Evidence |
| --- | --- | --- |
| Login, forced password change, logout, sessions | Done | `auth.spec.ts` (4 tests), same frozen `/autopilot/api/auth/*` backend |
| App shell, rail, phone menu, skip link, focus | Done | `nova.spec.ts` mobile menu journey |
| Theme Studio, first paint, device light/dark, server sync | Done | theme-choice-survives-reload journey |
| Home / overview, Chat (SSE, markdown, attachments, rename/reopen) | Done | chat streaming + attachment journeys |
| Agent tasks: create, events, report, judgement, follow-up, cancel, delivery, checkpoints, diff, screenshots | Done | agent-feed + agent task creation journeys |
| Workflows: library (knowledge/docs), mission, music, projects, tools | Done | workflows.spec.ts journeys |
| Models: routes/targets editing, providers, image generation | Done (editing) | workflows journeys; **catalog refresh/filter not in UI** (disclosed) |
| Routing policy card (mode, allowance, per-task limit, decisions, "why?") | **MISSING in React** | backend `GET/PUT /autopilot/api/routing`, `/routing/decisions/{id}`, `/routing/explain` intact and verified reachable (200) |
| Costs: per-day chart, monthly totals | Done | costs view; budget *editing* disclosed below |
| GPU: status, connect/stop (dry-run), provider checks | Partial | advanced policy/provider panels disclosed below |
| Settings: autopilot limits, GitHub connection, project memory, release controls | Done | settings-persist + release-guard journeys |
| Publication: approval, cancel, rollback | Done | Release view + guarded by installer |
| 3D Studio, screenshots, file previews | Done (external link, unchanged protected studio) | "3D Studio stays available inside the React shell" journey |
| Routing-as-text editor, manual prices, music cover-art blending, chat full-size image viewer, home deep links | **MISSING in React (deferred)** | backend intact; listed as required human decision |

### Deferred (disclosed, not silently lost)

Routing policy editor + decisions/explain; routing-as-text + manual prices; Models catalog refresh/filter;
provider keys & test call; Costs budget editing; GPU advanced policy/provider panels; music cover-art blending;
chat full-size image viewer; home deep links. These need implementation before or shortly after cutover, or an
explicit owner decision to drop them. **This is why the release below is classified "not fully verified": a
successful build is not functional parity.**

## Tests (exact commands, this run)

- `bash dev/test.sh` (same as CI): baseline worktree `f71e414`: **381 passed** / branch: first run 361 passed,
  1 failed (`test_mission.py::test_start_links_agent_task_and_owner_judges_it`, timing race while Chromium E2E ran
  concurrently); clean re-run on the branch: **362 passed, 0 failed** (19 fewer tests than baseline = exactly the
  deleted legacy `test_dashboard.py`).
- `cd /work/t36/tmp/opencode/nb/frontend && npm run build` (tsc -b + vite build): **passed** (no type errors).
- `npm run test:e2e` (Playwright, 13 specs): **14 passed (56.7 s)**, incl. login, chat streaming, agent task to
  completion, settings, mission, library, music, 3D Studio.
- Route checks (live preview): `/autopilot/` 200 React `#root`; `/autopilot/assets/*.js|css` 200;
  `/autopilot/react/` 200; `/autopilot/api/public|status|conversations|agent|routing` 200 JSON;
  `/autopilot/3d/` 200; legacy markers in served HTML: 0; `/autopilot/3d/%2e%2e/dashboard.html` → 404.

## Deployment: NOT PERFORMED

No deploy, restart, merge or push happened. The cutover sequence below is a proposal for the operator.

### Proposed operator release sequence

1. Push the archive ref to a private remote: `git push <remote> legacy-dashboard-archive` (currently missing —
   blocker).
2. Close the deferred gaps above, or accept them in writing.
3. Merge `nova/react-only`; CI builds `nova-react-build.tar.gz` for that commit.
4. Approve the exact commit in Dashboard → Release; installer stages it, `vac-autoupdate` verifies the asset
   matches the approved commit before installing.
5. Smoke tests on the live site: `/autopilot/` shows React login; sign in; one chat request; agent task;
   `/autopilot/3d/` still protected; Caddy log clean.
6. Monitor `app/health.py` restart diagnostics and LiteLLM error rate for one session before declaring success.

### Rollback triggers and action

Trigger: login/chat failure, missing assets (503 `react_build_missing`), health check regression. Action: request
Rollback in Release view (reverts to the previous release directory) and re-approve the previous commit; the legacy
UI can be restored from the archive tag afterwards if the owner decides to re-open the classic frontend.

## Remaining risks / required human decisions

- Remote archive backup missing (no remote in the agent workspace) — owner must push the tag before cutover.
- Deferred feature list above: implement or accept.
- `AGENTS.md` still says the dashboard is a single static file without a build step; this branch supersedes that
  rule (as the owner's React migration already does). Owner should update the rule text.
- The `test_mission` timing race exposed during this run is pre-existing (baseline passes only because of
  different ordering); it may flake in CI independent of this branch.

## Closure (2026-11-18)

- Cutover merged in PR #44; `/autopilot/` serves only `static/react/` (CI-built, fetched by the installer for the exact
  release commit). No classic-dashboard route or file remains in `main`.
- **Durable archive (supersedes the local tag above):** GitHub branch `archive/legacy-dashboard` at `0c7fbd3`, the last
  commit before the cutover; it contains `autopilot/app/static/dashboard.html`. Restore a file with
  `git checkout origin/archive/legacy-dashboard -- autopilot/app/static/dashboard.html`. Never served in production.
- Kept on purpose: `/autopilot/react/` alias (old links/bookmarks), `/autopilot/3d/`, `/autopilot/carbon/` (3D Studio),
  and the selfdev invariant checks that still mention `dashboard.html` (harmless: they only fire if that file returns).
