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
| ApprovalDialog instead of `confirm()` (Cancel focused for destructive actions) | `<dialog id="askDlg">`, JS `ask` | implemented |
| Task cost split: confirmed / estimated / free / unpriced, never "$0 confirmed" for unknown | `Router.tag_costs`, task field `cost`, JS `costChip` | implemented |
| Rebrand to NOVA | `BRAND` constant + visible copy in `dashboard.html`, studio page titles | implemented |
| Conversation stuck on a task that never started | `conversation_work` in `workspace.py` relinks a fresh task | implemented |
| Home return brief for current work / latest result | `HOME.focus`, `renderHomeBrief`, `openHomeResult`; nullable `conversation_id` in task detail | implemented |
| Outcome-first conversation workcard | `resultFacts`, `resultMetrics`; Evidence before Activity; Review or live-workspace primary action | implemented |
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
7. **Rezultatul este înaintea jurnalului tehnic.** Home alege prima lucrare activă, altfel ultima lucrare, și citește
   rezultatul real prin API. Cardul din conversație arată întâi rezultatul, fișierele, verificările și costul; fazele
   și evenimentele rămân în Activity. Un exit `null` este „unverified”, iar lipsa verificărilor este afișată explicit,
   fără succes inventat. Dacă legătura cu conversația lipsește sau detaliul nu poate fi încărcat, rămâne jurnalul
   proiectului.

## Deviations from the blueprint (and why)

- **No on-demand screenshot button / comparison in the stage:** the backend only stores captures the agent takes
  during a run; Review shows those. A capture endpoint is a runner-side change.
- **No "stopped" preview state:** the preview API does not report it.
- **Cost "pending" is shown as "estimated":** a late OpenRouter report updates the row from `estimate` to `provider`
  (existing reconciliation); there is no separate "reserved" amount, and budget reservation is not atomic yet (§18).
- **Navigation labels unchanged** (Workspace, Conversations, Projects, Models, Tools, Usage): Library/Settings pages
  do not exist yet.

## Verification

- `bash dev/test.sh` (includes `test_delivery_reports_only_what_github_and_the_installer_confirm` and
  `test_nova_work_surface_is_wired_to_real_endpoints_and_stays_sandboxed`,
  `test_task_cost_keeps_confirmed_estimated_and_unknown_apart`).
- `PORT=4173 bash dev/preview.sh` then `node dev/workspace-smoke.cjs`: desktop 1672×941, tablet 820×1180, phone
  390×844, no horizontal overflow, no page errors; captures in `dev/evidence/` (`stage-*`, `mobile-*` are new).
  The preview uses simulated services (label "DEMO / SIMULATED SERVICES").
- **Not verified:** the real server, real models, real GitHub pull requests, real provider costs.

## Verificare — rezultat la revenire (2026-11-16)

- `bash dev/test.sh tests/test_dashboard.py tests/test_agent.py::test_conversation_work_keeps_context_task_and_followups`:
  **17 passed**; apoi `bash dev/test.sh`: **271 passed in 33.81s**.
- `node dev/workspace-smoke.cjs`, cu Playwright din mediul temporar și preview-ul demo direct: toate traseele existente
  au trecut (dark/light, conversații active/finalizate/eșuate, Build/Review/Ship, desktop/tabletă/telefon și celelalte
  pagini). Capturile binare regenerate de acest smoke-test nu fac parte din diff.
- Parcurs suplimentar pe un preview izolat, `PORT=4174 .venv/bin/python dev/preview_server.py`, cu runnerul temporar
  `/tmp/opencode/nova-browser/after.cjs`: desktop 1440×1000 și Playwright iPhone 13 (390×844). Au trecut Home → Review,
  Review → Discover → jurnalul proiectului, lucrare activă → progres → rezultat fără verificări și afișarea metricilor
  înaintea Evidence/Activity. Rezultat: **11 capturi, zero overflow, zero `pageerror` și zero erori de consolă**.
- Dovezi: `/tmp/opencode/nova-browser-evidence/baseline.json`, cinci capturi `before-*.png`,
  `/tmp/opencode/nova-browser-evidence/after.json` și 11 capturi `after-*.png`. Modelul nu poate inspecta vizual
  imaginile; afirmațiile verificate sunt cele DOM, de navigare, consolă și dimensiuni, nu o apreciere estetică.
- Preview-ul proprietarului pe `/tasks/21/preview/autopilot/` trebuie repornit după schimbări Python; butonul Reload nu
  repornește procesul. Imaginea demo folosește un URL absolut `/autopilot/api/images/…`, care primește 401 prin acest
  prefix. Validarea fără erori a folosit preview-ul direct izolat; această limitare existentă nu este ascunsă de test.
- Costul validării: **0 USD**, fără apeluri către modele sau servicii plătite; runnerul este simulat. Soldul, tarifele
  furnizorilor și cheltuielile reale nu au fost verificate. Serverul real, modelele reale și GitHub rămân neverificate.

## Phase two (2026-11-16): routing, Knowledge, timeline, projects

Owner decision: routing is **automatic** within a configured allowance.

- `app/policy.py`: modes auto / economy / balanced / quality / manual. Pool = every target saved in the routes (no new
  provider or charge category appears by itself). Hard filters: required capabilities (tools, vision, image output,
  context), known price for paid models. Ranking evidence: judged task outcomes and call errors over 30 days, ignored
  below 5 calls / 3 tasks (neutral prior). Expected cost (price × expected tokens ÷ call success) is reserved
  atomically against `auto_allowance_usd` (month) and `auto_task_limit_usd` (per task) and released when the call is
  recorded. At most 3 attempts. Each decision is stored in `routing_decisions` and explained at
  `/autopilot/api/routing/decisions/{id}` (reason, ranked candidates, rejected models, cheaper alternative).
  Until an allowance is set, auto uses the saved routes exactly as before (no new paid automation).
  Limitation: reservations are in-process (single controller process, as deployed); a multi-process deployment would
  need them in SQLite.
- `app/knowledge.py`: Knowledge store (SQLite FTS5, LIKE fallback). Agent first prompts and conversation replies get
  decided/locked records plus the best matches for the project. Automatic writes only for verified runs
  (outcome completed and every reported check passed). UI: Library.
- `app/timeline.py`: one derived event history keyed by source ids (idempotent, replayable). GitHub/installer rows are
  written only from `Release.delivery()` observations. UI: Activity, project home.
- Project home (`/autopilot/api/projects/overview`): last work, unfinished tasks, costs, live version, knowledge,
  timeline, "Continue where I left off".
- Phones: bottom navigation, hidden while typing.

Retrieval check: `GET /autopilot/api/knowledge?q=landing` in the preview returns the seeded demo records
(`search: fts5`); `tests/test_knowledge.py` checks scope, prefix search, versions, locks, secrets and agent prompts.

## Mission Control (2026-11-16): un obiectiv activ, lucrări, runde reale de agent

- `app/mission.py` + tabelele `mission_objectives` / `mission_items` (SQLite): proprietarul are **un singur
  obiectiv activ** (celelalte stau parcate; reactivatea e explicită). Lucrările parcurg stările
  `idea → backlog → ready → running → review → verified` (sau `parked`); doar `ready` e eligibilă pentru execuție
  (`next`), iar dependențele (`depends_on`) blochează lucrările care vin după.
- „Start" este mereu **acțiune explicită a proprietarului**: creează un task real de agent (lucrarea e legată de
  `agent_task_id` și deschide proiectul). Runda nu se marchează verificată singură: proprietarul o revizuiește
  (înapoi cu notă, ori verificată). Starea se citește din înregistrarea agentului, nu se presupune.
- „Suggest plan" este singurul apel de model al view-ului: trece prin router (rolul `vellur-chat`), costul e
  înregistrat și afișat (confirmat de cost ledger, ori estimat), iar planul se salvează doar dacă proprietarul
  îl confirmă în dialog (formă validă: cel puțin 2 pași, cel mult 10).
- Dashboard: nav + `#missionCard` (cardul obiectivului cu buget lunar și estimare, cardul „Working now", secțiunile
  Ideas/Backlog/Ready/Working/Awaiting review/Verified/Parked cu acțiuni pe lucrare), 3 dialoguri (obiectiv, plan,
  return). Mobile-first: nav-ul de jos include Mission, ținte de minim 44 px, fără overflow orizontal.
- API: `GET /autopilot/api/mission/snapshot`, obiective `POST/PATCH /autopilot/api/mission/objectives`,
  lucrări `POST /autopilot/api/mission/items`, acțiuni `POST /autopilot/api/mission/items/{id}/start|return|verify|state`,
  plan `POST /autopilot/api/mission/items/{id}/suggest-plan|save-plan`. Totul în spatele login-ului existent
  (`admin` / `admin_write`); autentificarea înghețată nu a fost atinsă.

### Verificare (2026-11-16)

- `bash dev/test.sh`: **278 passed** (271 înainte de această felie + 7 noi în `tests/test_mission.py`: un singur
  obiectiv activ și park/reactivate, stări + next-eligibil cu dependențe, start → task real de agent → decizia
  proprietarului (înapoi cu notă / verificată), gardurile de ocupat și buget epuizat, validări, suggest-plan cu
  cost înregistrat).
- Preview izolat pe port 4174 (`dev/preview_server.py`, runner simulat, cost 0 USD): parcursul browser complet,
  desktop 1440×1000 și iPhone 390×844 — setare obiectiv, prindere idee, mutare în backlog, sugestie de plan
  **explicită** (cu cost afișat), confirmare și salvare, Ready, start cu confirmare în `askDlg`, lucrarea în
  „Working", la finalul rundei trece în „Awaiting review", înapoi în backlog cu notă, a doua rundă, verificare.
  **0 `pageerror`, 0 erori de consolă, fără overflow orizontal pe iPhone**.
- `node dev/workspace-smoke.cjs` (port 4173): **22 PASS** — traseele existente (Discover/Build/Review/Ship,
  desktop/tabletă/telefon, Models, Costs, Tools, Agent) nu au suferit regresii.
- **Neverificat:** serverul real, modelul real (sugestia de plan a folosit furnizorul simulat local din preview),
  costurile reale.
