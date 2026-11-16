# Task recovery after restarts (2026-11-16)

## The failure
"Error. The server restarted while this task was running. Send a follow-up to continue." This text came from
`AgentManager.recover()`, which runs when the **controller** (container `vellur-ai-autopilot`) starts. It marked every
in-flight task failed and **cancelled the agent in the runner** (container `agent`), although the runner is a separate
container whose OpenCode process may still have been working.

## Findings (code and failure-injection tests; production logs still needed)
1. **Controller restart = task killed.** Any controller restart (release install, `vac restart`, out-of-memory kill,
   crash) ended the running task, even when the runner was healthy.
2. **The agent's model token lived only in the controller's memory**, so a surviving agent could not call models after
   a controller restart.
3. **The runner saved the OpenCode session id only when a run ended.** If the runner container itself died mid-run,
   the session was lost and the task could not be continued at all. (Found by the real-process test below.)
4. **Memory pressure on the controller (256 MB limit):** the conversation card fetched up to 5000 events (each up to
   ~6 KB of tool output) every 3 s while a task ran. For a long task that is tens of MB built per poll — a plausible
   contributor to an out-of-memory kill, **not proven**.

Because a manual "continue" worked in the incident, the runner still had the session in memory: the evidence points to
a **controller-only restart**. What restarted it (release install, OOM, crash) must be confirmed from the server.

### Commands for the owner (read-only, on the VPS)
```bash
sudo docker inspect vellur-ai-autopilot --format 'started={{.State.StartedAt}} restarts={{.RestartCount}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}'
sudo journalctl -k --since "2 days ago" | grep -iE "oom|killed process" | tail -20
sudo docker events --since 48h --filter container=vellur-ai-autopilot --filter event=die --filter event=start --filter event=oom --until 0s
sudo journalctl -u vac-autoupdate --since "2 days ago" | tail -40   # release installs (if the unit exists)
free -m; sudo docker stats --no-stream
```
After this release: Activity shows a boot event per controller start ("clean shutdown" vs "UNCLEAN stop", memory at
the last heartbeat, release change), and `GET /autopilot/api/diagnostics/restarts` lists the last 30 boots and the
interrupted tasks.

## Recovery architecture
- The runner (own container) executes the agent; the controller only watches it. A run's context is persisted:
  `task_run_<id>` (run number, start time, cost at start), the event cursor (`tasks.cursor`), the model token hash
  (`kv proxy_token_<sha256>`), recovery attempts (`task_recoveries_<id>`). The runner persists its task state and now
  the session id as soon as it is known.
- On controller start, each in-flight task is recovered **in the background** (startup is not blocked):
  - runner says the run is still going → **re-attach**: keep importing events from the cursor; limits use the
    persisted start time and cost. No prompt is sent again.
  - runner says the run ended meanwhile → collect the result normally.
  - the agent process itself was interrupted (runner restarted) and a session exists → **automatic resume** of the same
    OpenCode session with a recovery prompt: inspect the workspace first, do not repeat finished writes or side effects,
    then finish the task. At most **2** automatic resumes per task; only when the agent workspace is available and the
    monthly budget is not used up (model calls still go through routing, allowance and limits).
  - the run never started (no session, never sent to a model) → start it again (nothing to duplicate).
  - task was being stopped → stays stopped. Publication in progress → paused for the owner ("verify GitHub before
    retrying"), never repeated blindly.
  - otherwise → a clear message: why, files are kept, and **Resume** (send a follow-up).
- The owner sees real states: "Execution was interrupted… checking the agent workspace", then "Reconnected: the agent
  kept working…" or "Workspace restored. Continuing from the last verified step (automatic recovery n of 2)".
- A normal shutdown no longer revokes the agent's model token (it may still be working); the token is revoked when the
  run really ends.

## Validation
- `tests/test_recovery_integration.py` uses the **real runner process** (`agent/runner.py`) and a fake OpenCode that
  writes files and runs a long tool:
  1. controller killed mid-tool, runner alive → re-attached, finished, OpenCode started once, every event imported once,
     token revoked at the end;
  2. controller and runner killed (SIGKILL) mid-tool → resumed automatically (2 runs, no owner message), step 1 not
     repeated, cost recorded once;
  3. resume limit reached → stops with a clear Resume message.
- `tests/test_health.py`: clean vs unclean boot records, release change, diagnostics endpoint.
- Not validated on the VPS or with real models.

## Known limitations
- If the controller restarts while the runner is also unavailable for ~2 minutes, the task ends with a Resume message.
- A file write that OpenCode was in the middle of when killed may be partial; the recovery prompt makes the agent check
  the workspace, but this is the agent's judgement, not a transaction.
- Reservations for automatic routing are in memory; in-flight reservations are dropped on restart (their usage is
  still recorded by the provider call that completes or fails).
