# NOVA React migration (P2, incremental preview)

Status: opt-in preview only. The production entry point stays `/autopilot/` (`autopilot/app/static/dashboard.html`).
The React app is served at `/autopilot/react/` when `frontend/` has been built into
`autopilot/app/static/react/` (gitignored). No production default was flipped, merged or deployed.

## Layout

- `frontend/` — Vite + React 18 + TypeScript + Carbon React app, hash routing, no second backend.
- `frontend/src/api/` — typed fetch client. Cookies, `credentials: include`, `X-Vellur-Request` on writes,
  same-origin checks and SSE over `fetch` are preserved from the existing dashboard.
- `frontend/src/views/` — one component per controller view; `frontend/src/components/` holds the shell,
  auth screens, errors, shared UI and a small React Markdown renderer (no `dangerouslySetInnerHTML`).
- `frontend/src/theme.tsx` — reads/writes the same `nova_theme` cookie and `ui_theme` setting, applies the ten
  existing token sets and the first-paint boot script in `frontend/index.html`.
- `autopilot/app/api.py` — additive `GET /autopilot/react[/...]` static route. Path containment, `no-store` for
  HTML, immutable caching for hashed assets, 404 when the build is absent.

Build off the VPS and copy only `autopilot/app/static/react/`:

```bash
cd frontend
npm ci
npm run typecheck
npm run build
```

CI runs the same `npm ci`, typecheck and production build on Node 22 before the Python suite. Browser journeys stay
local/CI-image work because they need Chromium; they are not part of the production test job.

## Migration matrix

Disposition: **M** migrated in React, **L** legacy fallback (still in `dashboard.html`), **D** intentionally
deferred from this slice, **N/A** not applicable to the preview.

| Area / screen | Disposition | React behaviour | Still in classic dashboard |
| --- | --- | --- | --- |
| Login, forced password change, logout, sessions | M | Same `/autopilot/api/auth/*` endpoints, cookie session, error text | Frozen forms and backend remain authoritative |
| App shell, desktop rail, phone menu, skip link, focus | M | Carbon UI Shell, responsive side nav | — |
| Theme Studio, first paint, device light/dark, server sync | M | All ten token sets; cookie `nova_theme`; `ui_theme` sync | — |
| Home / overview | M | Controller/GPU/month/agent stats, recent work, inference, projects, money, release | Deep links, advanced release controls |
| Chat history, search, create/delete conversation | M | Controller-backed list and routes | — |
| Chat send, model picker, SSE streaming, stop | M | `fetch` stream with abort, live Markdown | — |
| Chat attachments | M | Upload, pending list, remove, delivered attachments/images | Full previewer, image-size controls |
| Chat message editing | M | Reopen a user message in the composer, edit and resend; no separate regeneration control exists in the classic UI | — |
| Agent task list and history | M | State + judged outcome, cost, links | Filters beyond history |
| Agent create task | M | Prompt, repo, model, branch, tests checkbox, paid fallback | — |
| Agent task detail | M | Live event polling, report/tests/files/errors, follow-up, cancel, delivery | Checkpoints, diff review, screenshots, publication |
| Models/routes | M | Read routes/targets/providers, edit route order and save | Catalog refresh, provider keys, paid test call |
| Costs | M | Budget, usage, request ledger, refresh | Budget/provider editing surfaces |
| GPU | M | Session/provider state, start, protect 30 min, stop | Advanced settings, autopilot policy editing |
| Activity / release | M | Timeline plus release approval, cancel, rollback and guarded install controls | — |
| Library / project memory | M | Project list, records, search, expand, create, edit, lock/unlock and delete | — |
| Projects | M | Per-repo summary, tasks, cost, knowledge, inline task detail | Some project settings |
| Music | M | Catalog, release detail, create/edit/delete release, add/edit/delete tracks, artist profile | Cover-art linking and blending |
| Mission Control | M | Objective and queue editing, plan fields, parking, budget, scenes, self-development runs | Scene editing remains in 3D Studio |
| Tools / 3D Studio launcher | M | Links to protected 3D Studio and operational views | 3D Studio itself (unchanged) |
| GitHub connection | M | Write-only token save and connection check in Settings | — |
| Settings beyond theme | M | Autopilot, idle/session/budget settings, GitHub connection, project memory and release controls | Provider-specific advanced panels |
| Publication / installer approval | M | Release page records approval, cancels approval, requests rollback and shares the server-side installer guard | `vac` remains the server CLI |
| 3D Studio, screenshots, file previews | L | External link only | Existing protected studio |

## Verification

Prerequisite for the browser suite: the staging controller (`PORT=4173 bash dev/preview.sh`) and Vite dev
server (`npm run dev`, proxying `/autopilot` to 4173). Then:

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers npm run test:e2e
```

The suite has 13 browser tests: real login with forced password change, shell/API loading for Home plus thirteen
named views, theme persistence, phone navigation, live chat streaming with Markdown, chat rename/reopen/attachment,
library CRUD with lock/unlock, music release/track/artist editing, mission editing, settings persistence and GitHub
token save, guarded release controls, the 3D Studio iframe, and a complete simulated agent task lifecycle. Artifacts and
screenshots are kept outside the repository.

Visual inspection (vision-capable model, real Chromium screenshots at 1440 px and 390 px): every page route —
Home, Chat, Agent, Models, Costs, Activity, GPU, Library, Music, Tools, Mission, Theme Studio, Release and Settings.
Defects found and fixed in the recheck: assistant Markdown was shown as literal `**` text; theme previews did not use
each theme's own tokens; and phone layouts exposed 6–72 px of horizontal overflow from header actions and grid
min-content, now contained at 390 px.

## Build and serving

- Production build: ~2.1 MiB on disk including four local IBM Plex files; ~115 KiB initial JS gzip + ~125 KiB CSS
  gzip, with every view lazy-loaded as a small extra chunk and Carbon in a separate cached chunk.
- No dependency or browser binary belongs in Git. `frontend/node_modules/`, `frontend/*.tsbuildinfo` and
  `autopilot/app/static/react/` are ignored.
- The preview route is additive and can be removed by deleting the built folder (and, for a full rollback, the
  three route declarations in `autopilot/app/api.py`). `/autopilot/` is untouched.

## Production handoff and rollback

The React app is opt-in and same-origin. No Caddy change is needed: the existing `/autopilot/*` site already proxies
to the controller, and FastAPI serves `/autopilot/react/` plus its hashed assets.

Before any cutover, the owner must approve the exact commit and the release artifact must be built outside the VPS:

1. Run `cd frontend && npm ci && npm run typecheck && npm run build` on a trusted build machine (Node 22).
2. Copy the generated `autopilot/app/static/react/` directory to the release/install path on the VPS. Do not install
   Node, `node_modules` or a browser on the VPS.
3. Keep `/autopilot/` pointed at `dashboard.html`. Verify `/autopilot/react/` loads, login/CSRF/SSE work, and
   `/autopilot/` is byte-for-byte unaffected.
4. Only after a later, separately approved cutover should the controller or Caddy route `/autopilot/` to the React
   entry. That change is not part of this branch.

Rollback does not require a Caddy edit:

- Immediate: keep using `/autopilot/`; the classic dashboard never stopped serving.
- Disable the preview: remove/rename `autopilot/app/static/react/`; `/autopilot/react/` returns its normal 404 and
  the rest of the controller is unaffected.
- Full removal: also delete the three additive `/autopilot/react` route declarations in `autopilot/app/api.py`, then
  deploy normally. No database, session or theme migration is involved; both UIs share the same cookies and APIs.

The remaining distribution decision is how the generated folder enters a release (CI artifact vs installer-built
bundle). Until that is decided, production cutover is blocked even though the preview is reviewable.

## Remaining work

1. Owner visual review of the opt-in preview; production remains `dashboard.html` until explicit cutover approval.
2. Optional parity gaps: chat image preview/size controls, agent checkpoints/diff/screenshots/publication, Models
   catalog refresh/provider keys/paid test, Costs budget editing, GPU advanced policy, Music cover-art blending and
   provider-specific settings panels.
3. Choose how the CI-built frontend artifact reaches the release/install path. This is the only critical blocker
   before a production cutover; it does not block owner review of `/autopilot/react/`.
4. Run the Caddy/installer smoke tests against the static-serving change when it is proposed for production.
