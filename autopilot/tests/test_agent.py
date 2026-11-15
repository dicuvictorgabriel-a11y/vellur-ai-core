import asyncio
import base64
import json

import httpx
import pytest

from app.agent import AgentManager
from app.api import build_admin
from app.router import Router

ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def oc(typ, run=1, **part):
    """An OpenCode `run --format json` event as the runner relays it."""
    ev = {"type": typ, "sessionID": "ses_1", "run": run, "part": part}
    if typ == "error":
        ev = {"type": "error", "run": run, "error": {"name": "APIError", "data": {"message": part.get("message", "boom")}}}
    return ev


def tool(cmd, exit_code=0, run=1):
    return oc("tool_use", run, tool="bash", state={"status": "completed", "input": {"command": cmd, "description": cmd},
                                                     "output": "ok", "metadata": {"exit": exit_code}, "title": cmd})


GOOD_RUN = [oc("step_start"), oc("text", text="I will read the app first."),
            oc("tool_use", tool="read", state={"status": "completed", "input": {"filePath": "/w/app.py"}, "output": "x", "title": "app.py"}),
            tool("python -m pytest -q"), oc("step_finish", reason="tool-calls", tokens={"input": 100, "output": 20}),
            oc("text", text="## Summary\nAdded the endpoint.\n## Done\n- /health\n## Not done\nnothing\n## Tests\npytest passed\nSTATUS: COMPLETE"),
            oc("step_finish", reason="stop", tokens={"input": 50, "output": 30})]


class FakeRunner:
    def __init__(self):
        self.calls, self.state, self.polls, self.finish_after, self.meta, self.source = [], None, 0, 2, None, None
        self.runs = [GOOD_RUN]          # events produced by each run (first task message, then follow-ups)
        self.events, self.run, self.messages, self.shots = [], 0, [], {}

    def _begin(self):
        self.state, self.polls = "running", 0
        evs = self.runs[min(self.run, len(self.runs) - 1)]
        self.run += 1
        self.events += [dict(e, run=self.run) for e in evs]

    def handler(self, req: httpx.Request):
        assert req.headers["x-runner-secret"] == "r" * 32
        p = req.url.path
        self.calls.append((req.method, p))
        if req.method == "POST" and p == "/tasks":
            self.meta, self.state = json.loads(req.content), "created"
        elif p.endswith("/source"):
            self.source = req.content
        elif p.endswith("/start"):
            self._begin()
        elif p.endswith("/message"):
            self.messages.append(json.loads(req.content))
            self._begin()
        elif p.endswith("/cancel"):
            self.state = "cancelled"
        elif p.endswith("/diff"):
            return httpx.Response(200, json={"stat": " app.py | 2 +-", "diff": "--- a/app.py\n+++ b/app.py\n"})
        elif p.endswith("/files"):
            return httpx.Response(200, json={"files": [
                {"path": "app.py", "mode": "100644", "content_b64": base64.b64encode(b"print('x')\n").decode()},
                {"path": "old.txt", "deleted": True}]})
        elif p.endswith("/summary"):
            return httpx.Response(200, json={"files": [{"path": "app.py", "status": "M", "added": 2, "removed": 1},
                                                       {"path": "old.txt", "status": "D", "added": 0, "removed": 3}]})
        elif p.endswith("/events"):
            after = int(req.url.params.get("after", 0))
            return httpx.Response(200, json={"events": self.events[after:after + 400], "state": self.state})
        elif "/shots/" in p:
            name = p.rsplit("/", 1)[1]
            return httpx.Response(200, content=self.shots[name]) if name in self.shots else httpx.Response(404)
        elif p.endswith("/shots"):
            return httpx.Response(200, json={"shots": sorted(self.shots)})
        elif req.method == "GET":
            self.polls += 1
            if self.state == "running" and self.polls >= self.finish_after:
                self.state = "done"
            return httpx.Response(200, json={"state": self.state, "exit": 0, "changed": 2, "error": "", "log": "agent log line",
                                             "session": "ses_1", "run": self.run})
        return httpx.Response(200, json={"ok": True})


class FakeGitHub:
    def __init__(self):
        self.calls, self.auth = [], set()

    def handler(self, req: httpx.Request):
        self.auth.add(req.headers.get("authorization"))
        p, body = req.url.path, json.loads(req.content or b"{}")
        self.calls.append((req.method, p, body))
        if p == "/user/repos":
            return httpx.Response(200, json=[{"full_name": "o/r", "default_branch": "main", "private": True},
                                             {"full_name": "o/s", "default_branch": "dev", "private": False}])
        if p == "/repos/o/r/branches":
            return httpx.Response(200, json=[{"name": "feature"}, {"name": "main"}])
        if p == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": "main"})
        if p == "/repos/o/r/tarball/main":
            return httpx.Response(200, content=b"TARBALL")
        if p == "/repos/o/r/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "base1"}})
        if p == "/repos/o/r/git/commits/base1":
            return httpx.Response(200, json={"tree": {"sha": "tree0"}})
        if p.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob1"})
        if p.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree1"})
        if p.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "commit1"})
        if p.endswith("/git/refs") or "/git/refs/heads/" in p:
            return httpx.Response(201 if req.method == "POST" else 200, json={})
        if p.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/5"})
        return httpx.Response(404)


@pytest.fixture
def agents(rig):
    def build(**kw):
        ctrl = rig(agent_runner_url="http://agent:7070", agent_runner_secret="r" * 32, **kw)
        router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        runner, gh = FakeRunner(), FakeGitHub()
        mgr = AgentManager(ctrl, router, runner_transport=httpx.MockTransport(runner.handler),
                           github_transport=httpx.MockTransport(gh.handler), poll_seconds=0.01)
        admin = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router, agent=mgr)), base_url="https://ai.example")
        return ctrl, router, mgr, runner, gh, admin
    return build


async def wait_final(mgr, tid):
    for _ in range(500):
        if mgr.task(tid)["state"] in ("done", "failed", "cancelled", "timeout", "over_budget"):
            return mgr.task(tid)
        await asyncio.sleep(0.01)
    raise AssertionError("task did not finish")


async def test_task_runs_and_publishes_only_on_request(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    await admin.put("/autopilot/api/agent/github", headers=WRITE, json={"token": "github_pat_" + "a" * 30})
    ctrl.settings.update({"memory": "Use TypeScript strict."})
    r = await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "Add a health endpoint and tests", "repo": "o/r"})
    tid = r.json()["id"]
    t = await wait_final(mgr, tid)
    assert t["state"] == "done", t["error"]
    assert t["files_changed"] == 2 and t["base"] == "main"
    assert runner.source == b"TARBALL" and "Notes from the owner (always follow them):\nUse TypeScript strict." in runner.meta["prompt"]
    assert runner.meta["prompt"].rstrip().endswith("Add a health endpoint and tests") and "STATUS: COMPLETE" in runner.meta["prompt"]
    assert runner.meta["proxy_token"].startswith("vtask-") and "github_pat" not in json.dumps(runner.meta)   # token never leaves the controller
    assert router.authorize(runner.meta["proxy_token"]) is None                                            # revoked at the end
    assert not any(p.endswith("/git/refs") for _, p, _ in gh.calls)                                        # nothing pushed yet
    d = (await admin.get(f"/autopilot/api/agent/tasks/{tid}", headers=ADMIN)).json()
    assert d["log"] == "agent log line" and "app.py" in d["diff_stat"] and d["can_publish"] is True and d["legacy"] is False
    rep = d["report"]
    assert rep["outcome"] == "completed" and rep["claim"] == "COMPLETE" and rep["sections"]["not_done"] == "nothing"
    assert rep["tests"] == [{"command": "python -m pytest -q", "exit": 0, "status": "completed", "test": True}]
    assert [f["path"] for f in rep["files"]] == ["app.py", "old.txt"] and rep["tokens"] == {"input": 150, "output": 50}
    kinds = [e["kind"] for e in (await admin.get(f"/autopilot/api/agent/tasks/{tid}/events", headers=ADMIN)).json()["events"]]
    assert kinds[0] == "user" and "tool" in kinds and "text" in kinds and kinds[-1] == "report"
    p = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)
    assert p.json()["pr_url"] == "https://github.com/o/r/pull/5" and p.json()["branch"] == f"vellur/task-{tid}"
    tree = [b for m, path, b in gh.calls if path.endswith("/git/trees")][0]
    assert tree["base_tree"] == "tree0" and {"path": "old.txt", "mode": "100644", "type": "blob", "sha": None} in tree["tree"]
    pr = [b for m, path, b in gh.calls if path.endswith("/pulls")][0]
    assert pr["draft"] is True and pr["base"] == "main" and pr["head"] == f"vellur/task-{tid}"
    assert gh.auth == {"Bearer github_pat_" + "a" * 30}
    assert "github_pat" not in (await admin.get("/autopilot/api/agent", headers=ADMIN)).text
    assert "Agent report** (outcome: completed)" in pr["body"] and "python -m pytest -q" in pr["body"]
    # a follow-up continues the same session; publishing again moves the same branch (no second pull request)
    await admin.post(f"/autopilot/api/agent/tasks/{tid}/messages", headers=WRITE, json={"text": "Also add a README line"})
    t = await wait_final(mgr, tid)
    assert t["runs"] == 2 and runner.messages[0]["prompt"].startswith("Also add a README line")
    assert runner.messages[0]["proxy_token"].startswith("vtask-") and router.authorize(runner.messages[0]["proxy_token"]) is None
    await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)
    assert [m for m, p, _ in gh.calls if p.endswith("/pulls")] == ["POST"]
    assert ("PATCH", f"/repos/o/r/git/refs/heads/vellur/task-{tid}") in [(m, p) for m, p, _ in gh.calls]


async def test_repository_and_branch_pickers(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    r = (await admin.get("/autopilot/api/agent/repos", headers=ADMIN)).json()
    assert r == {"github_token_set": False, "repos": []} and not gh.calls          # no token → no GitHub call
    await admin.put("/autopilot/api/agent/github", headers=WRITE, json={"token": "github_pat_" + "a" * 30})
    r = (await admin.get("/autopilot/api/agent/repos", headers=ADMIN)).json()
    assert [x["name"] for x in r["repos"]] == ["o/r", "o/s"] and r["repos"][0]["private"] is True
    assert "github_pat" not in json.dumps(r)
    b = (await admin.get("/autopilot/api/agent/branches", params={"repo": "o/r"}, headers=ADMIN)).json()
    assert b == {"default": "main", "branches": ["main", "feature"]}
    bad = await admin.get("/autopilot/api/agent/branches", params={"repo": "not a repo"}, headers=ADMIN)
    assert bad.status_code == 400
    assert (await admin.get("/autopilot/api/agent/repos")).status_code in (401, 403)   # admin only


async def test_one_task_at_a_time_and_cancel(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    runner.finish_after = 10**9
    tid = (await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "Scratch task without repo"})).json()["id"]
    r = await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "Second task at once"})
    assert r.status_code == 409 and r.json()["error"] == "busy"
    await admin.post(f"/autopilot/api/agent/tasks/{tid}/cancel", headers=WRITE)
    assert (await wait_final(mgr, tid))["state"] == "cancelled"
    assert runner.source is None                                   # no repository → no snapshot, no GitHub call
    assert not gh.calls


async def test_cost_and_time_limits_stop_the_task(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    runner.finish_after = 10**9
    ctrl.settings.update({"agent_max_usd": 0.5})
    tid = (await mgr.create("Expensive task please", "vellur-coder"))["id"]
    await asyncio.sleep(0.05)
    ctrl.db.x("INSERT INTO usage(ts,month,role,target,provider,model,ok,cost_usd,cost_source,tag) VALUES(0,'x','r','t','p','m',1,0.75,'estimate',?)", (f"task:{tid}",))
    t = await wait_final(mgr, tid)
    assert t["state"] == "over_budget" and "cost limit" in t["error"] and t["cost_usd"] == 0.75


async def test_validation_unavailable_and_recovery(agents, rig):
    ctrl, router, mgr, runner, gh, admin = agents()
    for bad in ({"prompt": "x"}, {"prompt": "valid prompt", "repo": "not a repo"}, {"prompt": "valid prompt", "model": "gpt"}):
        assert (await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json=bad)).status_code == 400
    ctrl.budget.record_provider_charge("mock", "all", 50, ctrl.clock())
    import time
    ctrl.budget.record_provider_charge("mock", "now", 50, time.time())
    assert (await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "valid prompt"})).json()["error"] == "budget"
    ctrl.db.x("INSERT INTO tasks(created,updated,state,model,prompt) VALUES(1,1,'running','vellur-coder','p')")
    await mgr.recover()
    assert ctrl.db.one("SELECT state, error FROM tasks")["state"] == "failed"
    plain = rig()
    m2 = AgentManager(plain, Router(plain))
    assert m2.available()[0] is False
    with pytest.raises(Exception) as e:
        await m2.create("valid prompt here", "vellur-coder")
    assert e.value.code == "unavailable"


async def test_cut_off_reply_is_continued_automatically_and_never_reported_as_success(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    cut = [oc("text", text="Writing the whole new dashboard now: <html>..."), oc("step_finish", reason="length")]
    runner.runs = [cut, cut, cut]                     # the model keeps hitting its output limit
    tid = (await mgr.create("Redesign the whole interface", "vellur-coder"))["id"]
    t = await wait_final(mgr, tid)
    for _ in range(200):                              # wait for both automatic continuations
        if mgr.task(tid)["runs"] == 3 and mgr.task(tid)["state"] in ("done",) and not mgr._jobs:
            break
        await asyncio.sleep(0.01)
    t = mgr.task(tid)
    assert t["runs"] == 3 and len(runner.messages) == 2 and runner.messages[0]["prompt"].startswith("Your previous reply was cut off")
    assert t["state"] == "done" and t["outcome"] == "incomplete" and "output limit" in t["report"]["why"]
    notices = [e for e in mgr.events(tid) if e["kind"] == "notice"]
    assert len(notices) == 2


async def test_outcome_is_judged_not_assumed(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    # exit code 0 but no final report → unverified, not "completed"
    runner.runs = [[oc("text", text="I added four font files."), oc("step_finish", reason="stop")]]
    tid = (await mgr.create("Redesign the interface", "vellur-coder"))["id"]
    await wait_final(mgr, tid)
    await asyncio.sleep(0.05)
    assert mgr.task(tid)["outcome"] == "unverified"
    # claims COMPLETE but its last test run failed → needs attention
    runner.runs = [[tool("npm test", 1), oc("text", text="## Summary\nDone\nSTATUS: COMPLETE"), oc("step_finish", reason="stop")]]
    runner.events, runner.run = [], 0
    tid2 = (await mgr.create("Fix the failing test", "vellur-coder"))["id"]
    await wait_final(mgr, tid2)
    await asyncio.sleep(0.05)
    t2 = mgr.task(tid2)
    assert t2["outcome"] == "needs_attention" and t2["report"]["tests"][0]["exit"] == 1
    # provider error event → failed, with the provider's message
    runner.runs = [[oc("error", message="rate limited by provider")]]
    runner.events, runner.run = [], 0
    tid3 = (await mgr.create("Another task here", "vellur-coder"))["id"]
    await wait_final(mgr, tid3)
    await asyncio.sleep(0.05)
    assert mgr.task(tid3)["outcome"] == "failed" and "rate limited" in mgr.task(tid3)["report"]["why"]


async def test_screenshots_stream_and_diagnostics(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    runner.shots = {"home-mobile.png": PNG, "fake.png": b"<html>not a png</html>"}
    tid = (await mgr.create("Restyle the home page", "vellur-coder"))["id"]
    await wait_final(mgr, tid)
    await asyncio.sleep(0.05)
    shots = [e["data"]["name"] for e in mgr.events(tid) if e["kind"] == "shot"]
    assert shots == ["home-mobile.png"]                                # only real PNG images are accepted
    r = await admin.get(f"/autopilot/api/agent/tasks/{tid}/shots/home-mobile.png", headers=ADMIN)
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert (await admin.get(f"/autopilot/api/agent/tasks/{tid}/shots/..%2Fsecret.png", headers=ADMIN)).status_code == 404
    assert (await admin.get(f"/autopilot/api/agent/tasks/{tid}/shots/home-mobile.png")).status_code in (401, 403)
    assert mgr.task(tid)["report"]["shots"] == ["home-mobile.png"]
    # live stream: replays the timeline, then ends because the task is finished
    s = await admin.get(f"/autopilot/api/agent/tasks/{tid}/stream", headers=ADMIN)
    assert s.headers["content-type"].startswith("text/event-stream")
    assert "event: ev" in s.text and '"kind": "report"' in s.text and "event: end" in s.text
    last = mgr.events(tid)[-2]["id"]
    s2 = await admin.get(f"/autopilot/api/agent/tasks/{tid}/stream", headers={**ADMIN, "Last-Event-ID": str(last)})
    assert s2.text.count("event: ev") == 1                             # resumes after the last event it received
    ctrl.db.x("INSERT INTO usage(ts,month,role,target,provider,model,ok,out_tokens,cost_usd,cost_source,tag) "
              "VALUES(0,'x','r','t','deepseek','v4',1,16384,0.01,'provider',?)", (f"task:{tid}",))
    dg = (await admin.get(f"/autopilot/api/agent/tasks/{tid}", headers=ADMIN)).json()["diagnostics"]
    assert dg["requests"] == 1 and dg["last_reply_tokens"] == 16384 == dg["output_limit_tokens"]
