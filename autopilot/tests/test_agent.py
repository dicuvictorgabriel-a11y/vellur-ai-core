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


class FakeRunner:
    def __init__(self):
        self.calls, self.state, self.polls, self.finish_after, self.meta, self.source = [], None, 0, 2, None, None

    def handler(self, req: httpx.Request):
        assert req.headers["x-runner-secret"] == "r" * 32
        p = req.url.path
        self.calls.append((req.method, p))
        if req.method == "POST" and p == "/tasks":
            self.meta, self.state = json.loads(req.content), "created"
        elif p.endswith("/source"):
            self.source = req.content
        elif p.endswith("/start"):
            self.state = "running"
        elif p.endswith("/cancel"):
            self.state = "cancelled"
        elif p.endswith("/diff"):
            return httpx.Response(200, json={"stat": " app.py | 2 +-", "diff": "--- a/app.py\n+++ b/app.py\n"})
        elif p.endswith("/files"):
            return httpx.Response(200, json={"files": [
                {"path": "app.py", "mode": "100644", "content_b64": base64.b64encode(b"print('x')\n").decode()},
                {"path": "old.txt", "deleted": True}]})
        elif req.method == "GET":
            self.polls += 1
            if self.state == "running" and self.polls >= self.finish_after:
                self.state = "done"
            return httpx.Response(200, json={"state": self.state, "exit": 0, "changed": 2, "error": "", "log": "agent log line"})
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
        if p.endswith("/git/refs"):
            return httpx.Response(201, json={})
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
    assert t["state"] == "done" and t["files_changed"] == 2 and t["base"] == "main"
    assert runner.source == b"TARBALL" and runner.meta["prompt"].startswith("Project notes from the owner:\nUse TypeScript strict.")
    assert runner.meta["proxy_token"].startswith("vtask-") and "github_pat" not in json.dumps(runner.meta)   # token never leaves the controller
    assert router.authorize(runner.meta["proxy_token"]) is None                                            # revoked at the end
    assert not any(p.endswith("/git/refs") for _, p, _ in gh.calls)                                        # nothing pushed yet
    d = (await admin.get(f"/autopilot/api/agent/tasks/{tid}", headers=ADMIN)).json()
    assert d["log"] == "agent log line" and "app.py" in d["diff_stat"] and d["can_publish"] is True
    p = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)
    assert p.json()["pr_url"] == "https://github.com/o/r/pull/5" and p.json()["branch"] == f"vellur/task-{tid}"
    tree = [b for m, path, b in gh.calls if path.endswith("/git/trees")][0]
    assert tree["base_tree"] == "tree0" and {"path": "old.txt", "mode": "100644", "type": "blob", "sha": None} in tree["tree"]
    pr = [b for m, path, b in gh.calls if path.endswith("/pulls")][0]
    assert pr["draft"] is True and pr["base"] == "main" and pr["head"] == f"vellur/task-{tid}"
    assert gh.auth == {"Bearer github_pat_" + "a" * 30}
    assert "github_pat" not in (await admin.get("/autopilot/api/agent", headers=ADMIN)).text


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
