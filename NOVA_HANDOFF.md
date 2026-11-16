# NOVA handoff — where the work stands

Branch: `claude/eager-goodall-uj35ck` (restarted from `main` cc8389c after PR #14 merged). Details: `docs/NOVA.md`.

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

## Verification
`bash dev/test.sh` (tests: test_policy, test_knowledge, test_timeline + earlier suites); browser checks with
`dev/preview.sh` + `node dev/workspace-smoke.cjs` (simulated services) and the phase-two journey (Library, Activity,
Routing, Why dialog, project home; desktop + 390 px). Nothing validated against real providers, GitHub or the VPS.

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
