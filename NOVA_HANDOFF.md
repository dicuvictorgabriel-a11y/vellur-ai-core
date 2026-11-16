# NOVA handoff — where the work stands

Branch: `claude/eager-goodall-uj35ck` (based on `main` 056527c). Details: `docs/NOVA.md`.

## Done (wave 2 vertical slice + delivery truth from wave 1)
- NOVA rebrand (visible copy only), graphite/porcelain/lilac tokens, 64 px rail, toasts.
- Conversation workspace with Discover / Build / Review / Ship, status strip, timeline phases.
- `GET /autopilot/api/agent/tasks/{id}/delivery` + release stepper (tests in `tests/test_release.py`).
- Task cost chip: confirmed / estimated / free / unpriced (`Router.tag_costs`).
- In-page ApprovalDialog replaces every `confirm()`; toasts replace `alert()`.
- Browser journey extended (`dev/workspace-smoke.cjs`), new captures in `dev/evidence/`.

## Next, in order
1. Router modes Economy / Balanced / Quality / Manual (§19) on top of `app/router.py` routes.
2. Atomic budget reservation for concurrent tasks (§18); per-project mode persistence on the server.
3. Event ledger (§16) and Knowledge store (§23).

## Rules that still apply
Auth frozen, no localStorage, no build step, no paid calls, no deploy/merge by agents. Owner merges and approves
the exact commit in Activity → NOVA version.
