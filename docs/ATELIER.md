# Conversation workspace / Atelier

This branch evolves the existing Pearl shell into a quiet graphite / warm-paper workspace. The seeded pearl identity, theme cookie, single HTML architecture, model router, image safeguards and frozen authentication are preserved.

## Working behavior

- Discuss and Build share a conversation. Build is an explicit choice; requests are not automatically classified.
- The first Build request creates an existing agent task and persists its link in SQLite. Later Build messages continue that task. Recent discussion is passed as background on creation; discussion replies receive the recorded task outcome and report.
- Activity, files, checks, cost and review links use existing agent data. Process completion alone never means success. Detailed logs and previews remain in the project workspace.
- Deleting a conversation preserves its agent project. Existing agent budget, publication preferences and isolation rules still apply. This UI is not a new execution or billing security boundary.
- Dark/light themes, smaller identity pearl, compact navigation, reading widths, mobile drawer and 3D Studio surface styling share a restrained visual language.

## Verify / preview

Run `bash dev/test.sh`. Start `PORT=4173 bash dev/preview.sh`; Python edits require restarting this development process. Its database is temporary. The demo runner executes no commands and uses no real model, GPU or GitHub connection. A visible DEMO label identifies synthetic evidence.

With Playwright available, run `node dev/workspace-smoke.cjs`. Set `PLAYWRIGHT_MODULE` to a package path if needed and `CHROMIUM` to the browser executable. Captures are written to `dev/evidence/` (desktop 1672×941, tablet 820×1180, mobile 390×844).

## Limits

One linked task per conversation. Existing independent projects remain accessible through Projects. Agent activity is polled while the conversation is visible; full logs/diffs/preview stay in the project inspector. Visual/model quality, real provider billing and real GitHub publishing are not established by these simulated tests. No production changes are included.

## Owner-controlled installation

The draft PR must be reviewed and merged by the owner. Once GitHub tests pass for that exact main commit, use the existing **Activity → VELLUR version** approval flow if the installed version supports it. Otherwise use the documented PC deployment workflow from README, from a clean checkout of the approved main commit:

```powershell
git switch main
git pull --ff-only origin main
powershell -ExecutionPolicy Bypass -File deploy-from-pc.ps1
```

Do not run this from an unrelated or modified checkout. Deployment can restart VELLUR services: it is an owner action, not performed by this change. No credentials need to be pasted into chat.
