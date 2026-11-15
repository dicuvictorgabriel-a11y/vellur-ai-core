# VELLUR Agent — conversation-centred coding agent (15.11.2026)

The Agent tab is a conversation with an AI developer: you describe a task, watch the agent's real actions appear,
see screenshots it took, read a final report, reply to continue the same session, and approve a draft pull request.

## Investigation of the failed "UI redesign" test

The task said `done`, cost $0.0325 and changed four font files. This session had no access to the VPS database, so
the task's own records could not be read here. What the code and reproductions showed:

| Question | Evidence |
|---|---|
| Did the runner mark an incomplete task as done? | **Yes, by design of the old version.** `agent/runner.py` set `done` whenever `opencode run` exited with code 0. Nothing checked whether the request was fulfilled. |
| Can a model stop early with exit code 0? | **Yes, reproduced** with OpenCode 1.19.5 and a scripted server: (a) a reply cut off at the output limit (`finish_reason: length`, our limit is 16 384 tokens) ends the run with exit 0; (b) a model that simply ends its turn also exits 0. Provider errors exit 1 (`type: error` event). |
| Was the final message discarded? | It was not saved separately: the old runner kept only the last 40 KB of OpenCode's *formatted text* output as a raw log, shown as-is. No final report was requested from the model. |
| Were intermediate events available but hidden? | OpenCode can emit structured JSON events (`--format json`: `step_start`, `tool_use`, `text`, `step_finish` with `reason` and tokens, `error`). The old runner did not request them. |
| Screenshot tooling? | **None.** The agent image had no browser, so previews were impossible. |
| Token / time / cost limit? | Time and cost limits mark the task `timeout` / `over_budget`, not `done`, so they were not the cause. |

How to see the real cause of that task now: open it in the Agent tab. Tasks from before this version show an
"Older task" card with the model router's records: number of requests, and the size of the last reply. If the last
reply is at the output limit (16 384 tokens), it was cut off. The raw log is under "Raw agent log".

## What changed

- **Runner** (`agent/runner.py`): runs `opencode run --format json`; every event is stored in `events.jsonl`
  (long outputs clipped). Follow-up messages continue the **same OpenCode session** in the **same workspace**
  (`--session`). Task state is saved next to the workspace, so follow-ups work after a container restart. The
  latest 8 workspaces are kept.
- **Screenshots**: `vellur-screenshot <url-or-file> <name> [desktop|mobile]` in the agent image (Playwright's Chromium
  headless shell). Pictures are saved per task, copied by the controller only if they are real PNG files, and shown in
  the conversation. If the browser is missing, the command says so and the agent must report it. Nothing is
  exposed to the internet: dev servers run only inside the isolated container.
- **Controller** (`autopilot/app/agent.py`): imports the events into the `task_events` table (persistent history),
  sends the agent a working guide (explore, plan, small steps, run checks, finish with a fixed report
  and a `STATUS:` line), and builds a **final report** from recorded facts only.
- **Outcome is judged, not assumed.** Process state (`done`, `failed`, …) is separate from `outcome`:
  `completed` (agent says COMPLETE and its last checks passed), `partial`, `blocked`, `unverified` (no final report),
  `incomplete` (reply cut off), `needs_attention` (a check failed in its last run), `failed`, `stopped` (limit),
  `cancelled`.
- **Cut-off replies** are continued automatically up to 2 times ("continue in smaller steps").
- **Live progress**: `GET /autopilot/api/agent/tasks/{id}/stream` (server-sent events; reconnects resume from
  `Last-Event-ID`); the page reconnects when a phone brings the tab back. History comes from the server, so a refresh
  or leaving the page loses nothing.
- **GitHub**: "Create draft pull request" from the report; after a follow-up, "Update pull request" moves the same
  task branch. Never merged or deployed automatically. The PR body contains the agent's report and the checks it ran.

## API (all behind the existing login)

| Method | Path | |
|---|---|---|
| POST | `/autopilot/api/agent/tasks` | new task `{prompt, model, repo, base, visual_inspection?, auto_screenshots?}` |
| POST | `/autopilot/api/agent/tasks/{id}/messages` | follow-up `{text, model?, visual_inspection?, auto_screenshots?}` (same session) |
| GET | `/autopilot/api/agent/tasks/{id}` | task, latest report, diagnostics, `can_publish`, `can_message` |
| GET | `/autopilot/api/agent/tasks/{id}/events?after=` | timeline (polling) |
| GET | `/autopilot/api/agent/tasks/{id}/stream?after=` | timeline (SSE) |
| GET | `/autopilot/api/agent/tasks/{id}/diff` | code changes, on demand |
| GET | `/autopilot/api/agent/tasks/{id}/shots/{name}` | screenshot PNG |
| POST | `/autopilot/api/agent/tasks/{id}/cancel`, `/publish` | stop; create or update the draft PR |

## Limits and honest caveats

- One task runs at a time. Limits apply **per run** (each follow-up has its own minutes and dollars).
- The quality of the work depends on the model routed to `vellur-coder`. Free models often stop early or call tools
  badly; the report will now show it (`unverified`, `incomplete`) instead of `done`.
- The image build skips the browser by default. Build with `AGENT_BROWSER=1` when browser-based testing or screenshots are needed; if unavailable, optional captures are reported separately from test failures.
- Tested here end to end with real OpenCode 1.19.5, the real runner and controller and a scripted (non-model) server,
  including real screenshots, follow-ups, restart of the runner and page reloads. Not yet tested with a real model
  provider or on the VPS.

## Live Studio Mode (15.11.2026)

The Agent tab is now a studio: the conversation on the left, the **live preview** of the project on the right
(on a phone: the Preview button switches between them).

- **Persistent workspaces.** Each task keeps its workspace and OpenCode session on the server (the 12 most recent);
  follow-ups, page reloads and restarts continue from the same files.
- **Live preview.** A static site (`index.html` in the project root or in `dist/`, `build/`, `out/`, `public/`…) is
  served straight from the workspace. A project that needs a server declares it in `.vellur/preview.json`
  (`{"command": "npm run dev -- --port $PORT --host 127.0.0.1", "dir": "."}`); the runner starts it (one at a time)
  and proxies it. The pane reloads whenever the agent edits a file or finishes a run. Desktop / Phone width switch.
- **Safety of the preview.** Preview pages are code written by the agent, so they are served with
  `Content-Security-Policy: sandbox` (no `allow-same-origin`) inside a sandboxed frame: they cannot read the
  dashboard's cookies or call its API (verified in a browser: origin `null`, API call blocked). The address
  contains a random token that only a logged-in owner can create and that expires after 12 hours. Nothing listens
  publicly: the dev server is reachable only through the controller. Limits: WebSockets (hot reload) are not proxied,
  responses are capped at 20 MB, and links that scripts build as root-absolute paths may not resolve.
- **Visual verification.** Live preview remains independent of screenshots. Automatic desktop and mobile captures are off by default; select **Automatic screenshots** for one run to request them. **AI visual inspection** is a separate per-run choice and requires enabling image support for the selected model and its fallbacks in Agent settings. The agent uses `vellur-screenshot` and reads the image when available; capture alone does not prove inspection. Neither option carries over to the next message.
- **Publishing without pull requests.** The report has **Publish to <branch>**: one confirmed click commits the
  changes straight onto the branch the workspace started from. It is done only as a fast-forward: if the branch
  changed on GitHub meanwhile, VELLUR refuses and offers a pull request instead. After publishing, the workspace
  takes the published state as its new baseline. "Pull request instead" remains available. Nothing is ever
  published without that click.
- **Server updates without the terminal.** Superseded the same day: the server no longer follows `main`. It installs
  only a version the owner approved, after its tests passed, with rollback — see `docs/SELF-DEVELOPMENT.md`.

Tested here: real OpenCode + runner + controller with a scripted (non-model) server and a fake GitHub: preview
appears while the agent works, refreshes after a follow-up, sandbox isolation, automatic screenshots, direct publish
and its refusal when the branch moved, the updater's install / skip / restore logic with a fake installer. Not tested
here: a real model, a preview that needs a dev server for a large app (memory limit of the agent container is 2 GB),
the systemd timer on the VPS.
