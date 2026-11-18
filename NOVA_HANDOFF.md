# NOVA handoff — where the work stands

Sesiune curentă: branch local `nova/mission-control`, pornit de la `57fd83c`; fără push sau deploy (commit-ul
`c1c052c` „After run 3" a fost făcut automat de mediul de lucru, nu de agent). Istoric: `claude/eager-goodall-uj35ck`
(repornit din `main` cc8389c după PR #14). Detalii: `docs/NOVA.md`.

## 2026-11-17 — React-only candidate (branch `nova/react-only`)

Branch local pe copia izolată (bază `f71e414`): `/autopilot/` servește aplicația React, dashboard-ul legacy e
retras (recuperabil din tag-ul `legacy-dashboard-archive`), suita completă + 14 teste Playwright trec. Detalii,
matrice de paritate, checklist de cutover și rollback: `docs/NOVA-REACT-ONLY-CUTOVER.md`. Nimic împins sau
instalat; decizia aparține ownerului.

## Phase one (merged in #14)
Shell + tokens, Discover/Build/Review/Ship work surface, delivery endpoint + release stepper, honest cost chip,
ApprovalDialog, toasts.

## Phase two (this branch)
- **Automatic routing** `app/policy.py`: modes auto / economy / balanced / quality / manual over the owner's model
  pool (every target in the routes). Capabilities are never substituted, unpriced paid models are never auto-chosen,
  expected cost is reserved atomically against the monthly allowance + per-task limit, max 3 attempts, every decision
  logged with ranked candidates and evidence. Auto stays on saved routes until `auto_allowance_usd` > 0.
  API `/autopilot/api/routing*`. UI: Models → Routing, "Why this model?" (stage strip, decisions, timeline).
- **Knowledge** `app/knowledge.py`: SQLite + FTS5, namespaces, project scope, versions (409), locks, secret refusal;
  injected into agent first prompts and conversation replies; verified runs auto-recorded. UI: Library.
- **Timeline** `app/timeline.py`: derived, idempotent, replayable event history (task events, routing decisions,
  release events, GitHub/installer observations). UI: Activity (filters by category/project), project home.
- **Project home** (`view=project`): last work, unfinished, tasks, knowledge, timeline, costs, live version,
  "Continue where I left off". Home "Recent projects" opens it.
- **Phone**: bottom navigation (hidden while typing), project/library/activity layouts.

## Reliability fix (branch after #15)
Automatic task recovery after controller/runner restarts, restart diagnostics, incremental event polling in the
conversation card. Details and the owner's log commands: `docs/RECOVERY.md`. Open: confirm the incident's cause from
the server logs and update the Knowledge record `release:recovery-2026-11-16:incident`.

## Mission Control (această sesiune)
- `app/mission.py` + tabelele `mission_objectives` / `mission_items`: **un singur obiectiv activ** (celelalte
  parcate, reactivate explicită); lucrările parcurg `idea → backlog → ready → running → review → verified`
  (sau `parked`), doar `ready` e eligibilă (`next`), dependențele blochează ce vine după.
- „Start" e **acțiune explicită a proprietarului** (confirmare în `askDlg`): creează un task real de agent,
  lucrarea e legată de `agent_task_id` și deschide proiectul. Runda nu se verifică singură: proprietarul o
  revizuiește (înapoi cu notă, ori verificată). Starea se citește din înregistrarea agentului.
- „Suggest plan" e singurul apel de model al view-ului (rol `vellur-chat` prin router); costul e înregistrat și
  afișat, iar planul se salvează doar la confirmare (formă: 2–10 pași).
- Dashboard: `#missionCard` (card obiectiv cu buget lunar + estimare, „Working now", secțiuni cu acțiuni),
  3 dialoguri; nav-ul de jos (iPhone) include Mission. Autentificarea înghețată nu a fost atinsă.

## Rezultat la revenire (această sesiune)
- Home arată lucrarea activă sau ultimul rezultat: titlu, stare, rezumat real, fișiere, verificări, cost și acțiuni
  separate pentru Review/progres și jurnalul proiectului.
- Cardul din conversație pune rezultatul și metricile înaintea Evidence și Activity. La final deschide Review; în timpul
  rulării deschide spațiul live. Jurnalul complet și oprirea lucrării rămân disponibile.
- API-ul de detaliu al taskului returnează `conversation_id` nullable din legătura persistentă SQLite. Fără legătură sau
  la eroare de încărcare, interfața nu inventează un rezultat și oferă jurnalul proiectului.
- Autentificarea înghețată, recuperarea, publicarea și controalele de siguranță nu au fost modificate.

## Verification
- `bash dev/test.sh`: **278 passed in 35.40s** (271 înainte de Mission Control + 7 noi în `tests/test_mission.py`).
- `node dev/workspace-smoke.cjs` (port 4173): **22 PASS** — traseele existente (Discover/Build/Review/Ship,
  desktop/tabletă/telefon, Models, Costs, Tools, Agent) nu au suferit regresii.
- Parcurs nou pentru Mission Control, pe preview izolat (port 4174, runner simulat, **0 USD**), desktop 1440×1000
  și iPhone 390×844: obiectiv → idee → backlog → sugestie de plan (explicită, cu cost) → salvare → Ready → start
  (confirmare `askDlg`) → „Working" → „Awaiting review" → înapoi cu notă → a doua rundă → verificat.
  **0 `pageerror`, 0 erori de consolă, fără overflow orizontal.** Capturi: `/tmp/opencode/nova-browser-evidence/mission-*.png`.
- Dovezile anterioare rămân în `/tmp/opencode/nova-browser-evidence/` (`baseline.json`, `after.json`, `before-*.png`,
  `after-*.png`). Capturile nu au fost inspectate vizual de model; verificarea este DOM/browser, nu o evaluare estetică.
- Preview-ul cu prefix are o limitare separată: imaginea demo absolută `/autopilot/api/images/…` primește 401.
  După schimbări Python, preview-ul proprietarului necesită repornirea procesului, nu doar Reload.
- Cost al validării: **0 USD**, fără apeluri plătite. Soldul, prețurile și consumul real sunt necunoscute.
  Nimic nu a fost validat cu provideri reali, GitHub sau VPS; nimic nu a fost publicat sau instalat.

## Next, in order
1. Integrare Mission Control: proprietarul revizuiește `nova/mission-control`, merge și aprobe commit-ul exact
   în Activity → NOVA version (fără push/deploy făcut de agent).
2. Visual reference loop: attach a reference image in Build → agent captures the preview, compares, fixes; bounded to
   2 iterations (runner already captures screenshots; needs a comparison step and UI).
3. Preview: per-project-type detection report and "stopped" state from the runner; full-screen preview on phones.
4. Model economics page: per category × model success/cost table (data already in usage + tasks + routing_decisions).
5. Prompt caching flags per provider; tool-output compression in the runner; request dedup.
6. Persist workspace mode per project on the server (now sessionStorage per conversation).

## Rules that still apply
Auth frozen, no localStorage, no build step, no paid calls in tests, no deploy/merge by agents. The owner merges and
approves the exact commit in Activity → NOVA version.
