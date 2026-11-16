# NOVA handoff — where the work stands

Sesiune curentă: branch local `nova/self-evolution`, pornit de la `11d1201`; fără commit, push sau deploy.
Istoric: `claude/eager-goodall-uj35ck` (repornit din `main` cc8389c după PR #14). Detalii: `docs/NOVA.md`.

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

## Rezultat la revenire (această sesiune)
- Home arată lucrarea activă sau ultimul rezultat: titlu, stare, rezumat real, fișiere, verificări, cost și acțiuni
  separate pentru Review/progres și jurnalul proiectului.
- Cardul din conversație pune rezultatul și metricile înaintea Evidence și Activity. La final deschide Review; în timpul
  rulării deschide spațiul live. Jurnalul complet și oprirea lucrării rămân disponibile.
- API-ul de detaliu al taskului returnează `conversation_id` nullable din legătura persistentă SQLite. Fără legătură sau
  la eroare de încărcare, interfața nu inventează un rezultat și oferă jurnalul proiectului.
- Autentificarea înghețată, recuperarea, publicarea și controalele de siguranță nu au fost modificate.

## Verification
- `bash dev/test.sh`: **271 passed in 33.81s**; testele țintite pentru dashboard și legătura conversației: **17 passed**.
- `node dev/workspace-smoke.cjs`: toate traseele existente au trecut pe servicii simulate. Parcursul suplimentar
  Home/Review/Discover/jurnal și activ/final fără verificări a trecut pe desktop 1440×1000 și iPhone 13 390×844:
  **11 capturi, zero overflow și zero erori JavaScript/consolă**.
- Dovezi în `/tmp/opencode/nova-browser-evidence/`: `baseline.json`, `after.json`, cinci `before-*.png` și 11
  `after-*.png`. Capturile nu au fost inspectate vizual de model; verificarea este DOM/browser, nu o evaluare estetică.
- Preview-ul cu prefix are o limitare separată: imaginea demo absolută `/autopilot/api/images/…` primește 401. Testul
  final a folosit un preview direct izolat. După schimbări Python, preview-ul proprietarului necesită repornirea
  procesului, nu doar Reload.
- Cost al validării: **0 USD**, fără apeluri plătite. Soldul, prețurile și consumul real sunt necunoscute. Nimic nu a
  fost validat cu provideri reali, GitHub sau VPS; nimic nu a fost commit-uit, publicat sau instalat.

## Next, in order
1. Visual reference loop: attach a reference image in Build → agent captures the preview, compares, fixes; bounded to
   2 iterations (runner already captures screenshots; needs a comparison step and UI).
2. Preview: per-project-type detection report and "stopped" state from the runner; full-screen preview on phones.
3. Model economics page: per category × model success/cost table (data already in usage + tasks + routing_decisions).
4. Prompt caching flags per provider; tool-output compression in the runner; request dedup.
5. Persist workspace mode per project on the server (now sessionStorage per conversation).

## Rules that still apply
Auth frozen, no localStorage, no build step, no paid calls in tests, no deploy/merge by agents. The owner merges and
approves the exact commit in Activity → NOVA version.
