# NOVA — implementation record (blueprint v1.0, 2026-11-16)

NOVA is the temporary product codename (Neural Orchestration & Virtual Autonomy). Design owner: the blueprint
supplied by the owner (`NOVA_BLUEPRINT.md`, not committed). This file records what is implemented, what is verified,
and where the implementation deliberately differs. Status words: **implemented** (code + tests/browser check),
**proposed** (not built).

## Architecture map

| Blueprint area | Where it lives | Status |
|---|---|---|
| App shell, 64 px rail, drawer on phones | `autopilot/app/static/dashboard.html` (`body.rail`, `#railToggle`, cookie `nova_rail`) | implemented |
| Dark / light tokens (graphite, porcelain, lilac, nacre) | same file, CSS block "NOVA / Atelier layer" (last block wins) | implemented |
| Four modes of one task: Discover / Build / Review / Ship | `#modeBar`, `#stage`, JS `setMode`, `stageUpdate` | implemented |
| Status strip (state, current step, cost) | `#stStrip` | implemented |
| Timeline phases with real event times | JS `phaseRows` (reads `task_events`) | implemented |
| Preview stage: starting / ready / unavailable / error, viewport presets, real URL | JS `stPreview` → `POST /autopilot/api/agent/tasks/{id}/preview` | implemented |
| Review: files, checks, real screenshots, diff | JS `rvRender` → task report, `GET …/diff`, `GET …/shots/{name}` | implemented |
| Release stepper: Changes saved → PR open → Reviewed → Merged → Deployed | `Release.delivery()` in `autopilot/app/release.py`, `GET /autopilot/api/agent/tasks/{id}/delivery`, JS `shRender` | implemented |
| "Interrupted · changes saved" when the final report is lost but work exists | JS `refreshWork` (`lost`) and `phaseRows` | implemented |
| Toasts instead of `alert()` | JS `toast` | implemented |
| Rebrand to NOVA | `BRAND` constant + visible copy in `dashboard.html`, studio page titles | implemented |
| Conversation stuck on a task that never started | `conversation_work` in `workspace.py` relinks a fresh task | implemented |
| Unified event ledger with idempotency keys (§16), task state machine with leases (§17) | — | proposed |
| Immutable cost ledger, atomic budget reservation (§18), router modes Economy/Balanced/Quality (§19) | — | proposed |
| Knowledge namespaces and retrieval (§23), Library | — | proposed |
| Command palette beyond the existing search (Ctrl+K), resizable splits | — | proposed |

## Decisions (ADRs)

1. **Evolve the single-file dashboard; no rewrite, no build step.** `AGENTS.md` requires one static file without a
   build. The NOVA layer is appended CSS + one JS section + one stage element. The old agent inspector (Projects)
   stays as the full log; the conversation is the control plane.
2. **Delivery truth has three sources and no guesses.** Task record → "Changes saved"; GitHub REST (`pulls/{n}`,
   `pulls/{n}/reviews`, `compare/{merge}...{installed}`) → PR / Reviewed / Merged; the server installer's report
   (`release.installed`) → Deployed. If GitHub is unreachable a stage is `unknown`; a repository other than the
   self repo is `untracked` for deployment. Results are cached 30 s per task. No new write endpoint.
3. **Deploy approval is unchanged.** "Approve deploy…" opens Activity, where the existing exact-commit approval
   lives. The stage never installs or merges.
4. **Mode memory is per conversation in `sessionStorage`** (survives reloads, not devices). The blueprint asks for
   persistence per project; a server-side setting is the follow-up. `localStorage` stays forbidden.
5. **On phones the conversation opens first**; the four modes are tabs of the same task with 48 px targets.
6. **Names kept for compatibility:** domain, `/autopilot/` paths, cookies (`vellur_theme`), model ids
   (`vellur-coder` …), the `X-Vellur-*` headers, the hidden login username, repository and server paths, and
   server-side error prefixes / agent prompt text ("VELLUR AI (…)", covered by existing tests).

## Deviations from the blueprint (and why)

- **No on-demand screenshot button / comparison in the stage:** the backend only stores captures the agent takes
  during a run; Review shows those. A capture endpoint is a runner-side change.
- **No "stopped" preview state:** the preview API does not report it.
- **Cost chip says "so far / recorded", not "confirmed / pending":** the per-task figure does not carry the
  provider-confirmation flag yet (cost-ledger wave).
- **Native `confirm()` remains for approvals** (stop task, prepare PR, install): an in-page ApprovalDialog is proposed.
- **Navigation labels unchanged** (Workspace, Conversations, Projects, Models, Tools, Usage): Library/Settings pages
  do not exist yet.

## Verification

- `bash dev/test.sh` (includes `test_delivery_reports_only_what_github_and_the_installer_confirm` and
  `test_nova_work_surface_is_wired_to_real_endpoints_and_stays_sandboxed`).
- `PORT=4173 bash dev/preview.sh` then `node dev/workspace-smoke.cjs`: desktop 1672×941, tablet 820×1180, phone
  390×844, no horizontal overflow, no page errors; captures in `dev/evidence/` (`stage-*`, `mobile-*` are new).
  The preview uses simulated services (label "DEMO / SIMULATED SERVICES").
- **Not verified:** the real server, real models, real GitHub pull requests, real provider costs.

## Knowledge

The repository has no Knowledge store or retrieval API yet (§23 is proposed). Until it exists, this file,
`docs/ATELIER.md`, `docs/AGENT.md` and `AGENTS.md` are the record; retrieval check:
`grep -rn "delivery" docs/NOVA.md AGENTS.md`.
