import asyncio
import base64
import json
import importlib.util
from pathlib import Path

import httpx
import pytest

from app.agent import AgentManager
from app.api import build_admin
from app.router import Router

ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
GLB = b"glTF\x02\x00\x00\x00\x10\x00\x00\x00" + b"0" * 4


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
            return httpx.Response(200, json={"files": getattr(self, "files", None) or [
                {"path": "app.py", "mode": "100644", "content_b64": base64.b64encode(b"print('x')\n").decode()},
                {"path": "old.txt", "deleted": True}]})
        elif p.endswith("/checkpoints") or p.endswith("/restore"):
            cps = getattr(self, "cps", [{"id": "b" * 40, "ts": 1, "label": "Original version (before the agent's changes)", "base": True}])
            if req.method == "POST":
                body = json.loads(req.content)
                label = body.get("label") or "Restored: " + next(c["label"] for c in cps if c["id"] == body["id"])
                cps = [{"id": f"{len(cps):040x}", "ts": 2, "label": label}] + cps
                self.cps = cps
            return httpx.Response(200, json={"id": cps[0]["id"], "changed": 1, "checkpoints": cps})
        elif p.endswith("/models"):
            return httpx.Response(200, json={"models": [
                {"path": "models/scene.glb", "name": "scene.glb", "format": "glb", "size": len(GLB), "mtime": 1.5,
                 "previewable": True, "source": "repo", "reason": ""}], "blender": False})
        elif "/asset/" in p:
            self.asset_calls = getattr(self, "asset_calls", []) + [p]
            return httpx.Response(200, content=GLB, headers={"content-type": "model/gltf-binary"})
        elif p.endswith("/blend-export"):
            body = json.loads(req.content)
            return httpx.Response(200, json={"model": {"path": "@derived/scene-abc123.glb", "name": body["path"],
                                                       "format": "glb", "size": len(GLB), "mtime": 2.0, "previewable": True,
                                                       "source": "derived", "sourcePath": body["path"]}})
        elif p.endswith("/preview-info"):
            return httpx.Response(200, json={"mode": "static", "root": "dist", "why": "", "running": False})
        elif "/preview/" in p:
            self.preview_calls = getattr(self, "preview_calls", []) + [(req.method, p, req.url.query.decode())]
            if p.endswith("/app.css"):
                return httpx.Response(200, content=b"body{}", headers={"content-type": "text/css"})
            tid = p.split("/")[2]      # the real runner already keeps links inside /tasks/<id>/preview/
            if p.endswith("/go"):
                return httpx.Response(302, headers={"location": f"/tasks/{tid}/preview/autopilot/"})
            return httpx.Response(200, content=f'<html><link href="/tasks/{tid}/preview/app.css"><script src="/tasks/{tid}/preview/assets/a.js"></script><a href="//cdn.x/y">x</a></html>'.encode(),
                                  headers={"content-type": "text/html; charset=utf-8"})
        elif p.endswith("/rebaseline"):
            self.rebaselined = True
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
                                             "session": "ses_1", "run": self.run,
                                             "activity": [{"cmd": "blender -b scene.blend", "cpu_s": 61.0, "elapsed_s": 70}]
                                             if self.state == "running" else []})
        return httpx.Response(200, json={"ok": True})


class FakeGitHub:
    def __init__(self):
        self.calls, self.auth, self.head = [], set(), "base1"
        self.head_failures = 0
        self.user_status, self.user_message = 200, "Bad credentials"
        self.patch_headers, self.patch_message = {}, "not a fast forward"
        self.branch_protected, self.repo_push = False, True

    def handler(self, req: httpx.Request):
        self.auth.add(req.headers.get("authorization"))
        if req.method == "PATCH" and getattr(self, "patch_error", False):
            raise httpx.ConnectError("connection lost", request=req)
        if req.method == "PATCH" and getattr(self, "patch_status", None):
            return httpx.Response(self.patch_status, json={"message": self.patch_message}, headers=self.patch_headers)
        p, body = req.url.path, json.loads(req.content or b"{}")
        self.calls.append((req.method, p, body))
        if p.endswith("/git/trees") and getattr(self, "trees_forbidden", False):
            return httpx.Response(403, json={"message": "Resource not accessible by personal access token"},
                                  headers={"x-accepted-github-permissions": "contents=write"})
        if p == "/user":
            if self.user_status != 200:
                return httpx.Response(self.user_status, json={"message": self.user_message})
            return httpx.Response(200, json={"login": "octo", "id": 7})
        if p == "/user/repos":
            return httpx.Response(200, json=[{"full_name": "o/r", "default_branch": "main", "private": True},
                                             {"full_name": "o/s", "default_branch": "dev", "private": False}])
        if p == "/repos/o/r/branches":
            return httpx.Response(200, json=[{"name": "feature"}, {"name": "main"}])
        if p == "/repos/o/r/branches/main":
            return httpx.Response(200, json={"name": "main", "protected": self.branch_protected,
                                             "commit": {"sha": self.head}})
        if p in ("/repos/o/r", "/repos/old/r"):
            if getattr(self, "canonical_missing", False):
                return httpx.Response(200, json={"default_branch": "main"})
            return httpx.Response(200, json={"default_branch": "main", "full_name": "o/r",
                                             "private": True, "permissions": {"admin": False, "push": self.repo_push,
                                                                              "pull": True}})
        if p.startswith("/repos/") and p.count("/") == 3:
            if getattr(self, "canonical_missing", False):
                return httpx.Response(200, json={"default_branch": "main"})
            return httpx.Response(200, json={"default_branch": "main", "full_name": p[7:]})
        if p in ("/repos/o/r/tarball/main", "/repos/o/r/tarball/" + self.head):
            return httpx.Response(200, content=b"TARBALL")
        if p == "/repos/o/r/git/ref/heads/main":
            if self.head_failures:
                self.head_failures -= 1
                return httpx.Response(503, json={"message": "temporarily unavailable"})
            return httpx.Response(200, json={"object": {"sha": self.head}})
        if p == "/repos/o/r/git/commits/" + self.head:
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
            if getattr(self, "pulls_fail", 0):
                self.pulls_fail -= 1
                return httpx.Response(403, json={"message": "Resource not accessible by personal access token"})
            self.pr_n = getattr(self, "pr_n", 4) + 1
            return httpx.Response(201, json={"html_url": f"https://github.com/o/r/pull/{self.pr_n}"})
        if "/pulls/" in p:
            st = getattr(self, "pr_state", "open")
            return httpx.Response(200, json={"state": "closed" if st != "open" else "open", "merged": st == "merged"})
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
        if mgr.task(tid)["state"] in ("done", "failed", "cancelled", "timeout", "over_budget") and tid not in mgr._jobs:
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
    assert kinds[0] == "user" and "tool" in kinds and "text" in kinds and "report" in kinds
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


async def test_publish_waits_for_a_final_task_cleanup(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    await admin.put("/autopilot/api/agent/github", headers=WRITE, json={"token": "github_pat_" + "a" * 30})
    tid = (await admin.post("/autopilot/api/agent/tasks", headers=WRITE,
                            json={"prompt": "Publish just after the run ends", "repo": "o/r"})).json()["id"]
    await wait_final(mgr, tid)

    async def cleanup():                       # the database says done, but the job is still in its finally block
        await asyncio.sleep(0.15)
        mgr._jobs.pop(tid, None)

    mgr._jobs[tid] = asyncio.create_task(cleanup())
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)
    assert r.status_code == 200 and r.json()["pr_url"].startswith("https://github.com/o/r/pull/")


@pytest.mark.parametrize("state", ["merged", "closed"])
async def test_publish_after_merged_or_closed_pr_opens_a_new_pr_on_a_fresh_branch(agents, state):
    """Owner incident: the old PR (#35) was merged; Retry pull request force-moved its branch and kept
    pointing at the merged PR, so no new PR ever appeared."""
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    first = (await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)).json()
    gh.pr_state, gh.calls = state, []
    r = (await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)).json()
    assert r["pr_url"] != first["pr_url"] and r["branch"].startswith(f"vellur/task-{tid}-r")
    assert not [p for m, p, _ in gh.calls if m == "PATCH"]            # the merged branch is never force-moved
    assert [b["head"] for m, p, b in gh.calls if p.endswith("/pulls")] == [r["branch"]]
    # a third click while the new PR is open updates it: no duplicate PR
    gh.pr_state, gh.calls = "open", []
    again = (await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)).json()
    assert again["pr_url"] == r["pr_url"] and not [p for m, p, _ in gh.calls if p.endswith("/pulls")]


async def test_publish_pr_failure_is_reported_and_retry_recovers(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    gh.pulls_fail = 1
    j = (await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)).json()
    assert j["error"] == "github_forbidden" and j["details"]["status"] == 403 and j["details"]["operation"] == "POST /pulls"
    assert not mgr.task(tid)["pr_url"]
    ok = (await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)).json()
    assert ok["pr_url"].startswith("https://github.com/o/r/pull/")
    assert (await runner_files_intact(runner))


async def runner_files_intact(runner):
    return runner.state != "cancelled"


async def test_stale_finished_job_does_not_block_and_running_job_does(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    done = asyncio.get_running_loop().create_future(); done.set_result(None)
    mgr._jobs[tid] = done                                    # left behind by an interrupted finalization
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)
    assert r.status_code == 200 and tid not in mgr._jobs
    live = asyncio.get_running_loop().create_future()
    mgr._jobs[tid] = live                                    # a genuinely running job keeps the task protected
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)
    assert r.status_code == 409 and "still finishing" in r.json()["message"]
    live.cancel()


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


async def test_github_connection_is_verified_and_access_is_diagnosable(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    token = "github_pat_" + "a" * 30
    r = await admin.get("/autopilot/api/agent/github/check", headers=ADMIN)
    assert r.status_code == 400 and r.json()["error"] == "github_auth"
    gh.user_status, gh.user_message = 401, "Bad credentials"
    await admin.put("/autopilot/api/agent/github", headers=WRITE, json={"token": token})
    state = await admin.get("/autopilot/api/agent", headers=ADMIN)
    j = state.json()
    assert j["github_token_set"] is True and j["github"]["ok"] is False and "401" in j["github"]["message"]
    assert token not in state.text
    gh.user_status = 200
    j = (await admin.get("/autopilot/api/agent", headers=ADMIN)).json()
    assert j["github"] == {"ok": True, "checked": True, "login": "octo"}
    gh.branch_protected = True
    r = await admin.get("/autopilot/api/agent/github/check",
                        params={"repo": "o/r", "branch": "main"}, headers=ADMIN)
    j = r.json()
    assert j["ok"] is True and j["login"] == "octo" and j["repo"]["canonical"] == "o/r"
    assert j["repo"]["can_push"] is True and j["repo"]["branch"]["protected"] is True
    assert token not in r.text


async def test_github_errors_are_classified_instead_of_blaming_the_token(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    token = "github_pat_" + "a" * 30
    mgr.set_github_token(token)
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    cases = [
        (401, "Bad credentials", {}, "github_auth", "401"),
        (403, "Resource not accessible by personal access token",
         {"x-accepted-github-permissions": "contents=write"}, "github_forbidden", "contents: read and write"),
        (403, "protected branch hook declined", {}, "github_protected", "branch protection"),
        (403, "API rate limit exceeded", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "123"},
         "github_rate_limited", "rate limit"),
        (404, "Not Found", {}, "github_not_found", "not found"),
        (422, "Update is not a fast forward", {}, "github_rejected", "422"),
    ]
    for status, message, headers, code, fragment in cases:
        gh.patch_status, gh.patch_message, gh.patch_headers = status, message, headers
        r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
        j = r.json()
        assert r.status_code == 502 and j["error"] == code, (status, j)
        assert fragment in (j["message"] + " " + json.dumps(j.get("details", {}))).lower(), (status, j)
        assert token not in r.text
        assert not ctrl.db.kv_get(f"task_rebaseline_failed_{tid}")


async def test_publish_direct_names_missing_write_access_and_protected_branch(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    token = "github_pat_" + "a" * 30
    mgr.set_github_token(token)
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    gh.patch_status, gh.patch_message = 403, "Resource not accessible by personal access token"
    gh.repo_push = False
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
    j = r.json()
    assert j["error"] == "github_forbidden" and "no write access" in j["message"]
    assert j["details"]["access"]["can_push"] is False and token not in r.text
    gh.patch_message, gh.repo_push, gh.branch_protected = "protected branch hook declined", True, True
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
    j = r.json()
    assert j["error"] == "github_protected" and "is protected" in j["message"]
    assert j["details"]["access"]["branch"]["protected"] is True and token not in r.text


async def test_one_task_at_a_time_and_cancel(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    runner.finish_after = 10**9
    tid = (await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "Scratch task without repo"})).json()["id"]
    for _ in range(100):                                           # the command running right now is visible
        if mgr.task(tid)["activity"]:
            break
        await asyncio.sleep(0.01)
    assert mgr.task(tid)["activity"][0]["cmd"] == "blender -b scene.blend"
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
    await mgr.recover()       # recovery runs in the background; with the budget used up it must not resume
    row = await wait_final(mgr, ctrl.db.one("SELECT id FROM tasks")["id"])
    assert row["state"] == "failed" and "budget" in row["error"]
    plain = rig()
    m2 = AgentManager(plain, Router(plain))
    assert m2.available()[0] is False
    with pytest.raises(Exception) as e:
        await m2.create("valid prompt here", "vellur-coder")
    assert e.value.code == "unavailable"


async def test_recovery_never_leaves_publishing_and_requires_reconciliation(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    tid = (await mgr.create("Make a change", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    mgr._set(tid, state="publishing")
    await mgr.recover()
    t = mgr.task(tid)
    assert t["state"] == "done" and t["outcome"] == "completed"
    assert t["report"]["publication"]["status"] == "failed"
    assert ctrl.db.kv_get(f"task_rebaseline_failed_{tid}") is True


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


async def test_outcome_uses_the_last_check_not_a_stale_earlier_one(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    runner.runs = [[tool("python -m pytest -q", 1), tool("bash dev/test.sh", 0),
                    oc("text", text="## Summary\nFixed\nSTATUS: COMPLETE"), oc("step_finish", reason="stop")]]
    tid = (await mgr.create("Fix the failing checks", "vellur-coder"))["id"]
    await wait_final(mgr, tid)
    await asyncio.sleep(0.05)
    t = mgr.task(tid)
    assert t["outcome"] == "completed" and [c["exit"] for c in t["report"]["tests"]] == [1, 0]
    runner.runs = [[tool("bash dev/test.sh", 0), tool("npx tsc --noEmit", 2),
                    oc("text", text="## Summary\nDone\nSTATUS: COMPLETE"), oc("step_finish", reason="stop")]]
    runner.events, runner.run = [], 0
    tid2 = (await mgr.create("Fix the type error", "vellur-coder"))["id"]
    await wait_final(mgr, tid2)
    await asyncio.sleep(0.05)
    t2 = mgr.task(tid2)
    assert t2["outcome"] == "needs_attention" and "npx tsc --noEmit" in t2["report"]["why"]


async def test_a_transient_github_error_never_leaves_an_unpublishable_task(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    await admin.put("/autopilot/api/agent/github", headers=WRITE, json={"token": "github_pat_" + "a" * 30})
    gh.head_failures = 1
    tid = (await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "Add tests", "repo": "o/r"})).json()["id"]
    t = await wait_final(mgr, tid)
    assert t["state"] == "done" and t["base_sha"] == "base1"
    gh.head_failures = 3
    runner.calls.clear()
    tid2 = (await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "Add more tests", "repo": "o/r"})).json()["id"]
    t2 = await wait_final(mgr, tid2)
    assert t2["state"] == "failed" and "could not read the branch" in t2["error"]
    assert not any(p == "/source" for _, p in runner.calls)          # no workspace, so there is nothing to leave half-published


async def test_long_task_history_is_paged(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    tid = (await mgr.create("Long task", "vellur-coder"))["id"]
    await wait_final(mgr, tid)
    await asyncio.sleep(0.05)
    for i in range(5):
        mgr._ev(tid, 1, "text", text=f"page line {i}")
    seen, after, pages = [], 0, 0
    while True:
        r = (await admin.get(f"/autopilot/api/agent/tasks/{tid}/events?after={after}&limit=2", headers=ADMIN)).json()
        pages += 1
        seen += [e["id"] for e in r["events"]]
        if r["events"]:
            after = r["events"][-1]["id"]
        if not r["has_more"]:
            break
    assert pages > 2 and seen == sorted(set(seen)) == list(range(seen[0], seen[0] + len(seen)))
    lines = [e["data"].get("text", "") for e in (await admin.get(f"/autopilot/api/agent/tasks/{tid}/events?limit=1000",
                                                                 headers=ADMIN)).json()["events"]]
    assert [x for x in lines if x.startswith("page line")] == [f"page line {i}" for i in range(5)]


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


async def test_no_provider_credit_is_explained_and_another_model_can_continue(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    runner.runs = [[oc("error", message="VELLUR AI (provider_credit): openrouter:anthropic/x: no credit left at the provider")],
                   GOOD_RUN]
    tid = (await mgr.create("Build the scene", "vellur-max"))["id"]
    await wait_final(mgr, tid)
    await asyncio.sleep(0.05)
    t = mgr.task(tid)
    assert t["outcome"] == "blocked" and t["report"]["provider_credit"] is True and "no credit" in t["report"]["why"]
    assert not any("exited with code" in e for e in t["report"]["errors"])
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/messages", headers=WRITE,
                         json={"text": "Continue the task from the beginning.", "model": "vellur-coder"})
    assert r.status_code == 200
    await wait_final(mgr, tid)
    for _ in range(100):
        if mgr.task(tid)["outcome"]:
            break
        await asyncio.sleep(0.01)
    assert mgr.task(tid)["model"] == "vellur-coder" and runner.messages[0]["model"] == "vellur-coder"
    assert mgr.task(tid)["outcome"] == "completed"


async def test_live_preview_is_sandboxed_and_needs_a_link_from_a_logged_in_owner(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    tid = (await mgr.create("Build the landing page", "vellur-coder"))["id"]
    await wait_final(mgr, tid)
    assert (await admin.post(f"/autopilot/api/agent/tasks/{tid}/preview")).status_code in (401, 403)      # login required
    info = (await admin.post(f"/autopilot/api/agent/tasks/{tid}/preview", headers=WRITE)).json()
    assert info["mode"] == "static" and info["url"].startswith("/autopilot/preview/") and len(info["url"]) > 45
    r = await admin.get(info["url"])                                   # the sandboxed frame sends no cookie: the link is the key
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]
    assert "allow-same-origin" not in r.headers["content-security-policy"]
    assert f'href="{info["url"]}app.css"'.encode() in r.content and f'src="{info["url"]}assets/a.js"'.encode() in r.content
    assert b'href="//cdn.x/y"' in r.content                             # absolute URLs are left alone
    assert b"/tasks/" not in r.content
    go = await admin.get(info["url"] + "go")
    assert go.status_code == 302 and go.headers["location"] == info["url"] + "autopilot/"
    pre = await admin.options(info["url"] + "autopilot/api/x", headers={"Access-Control-Request-Headers": "content-type,x-vellur-request"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-headers"] == "content-type,x-vellur-request"
    css = await admin.get(info["url"] + "app.css?v=2")
    assert css.headers["content-type"].startswith("text/css") and css.headers["access-control-allow-origin"] == "*"
    assert runner.preview_calls[-1] == ("GET", f"/tasks/{tid}/preview/app.css", "v=2")
    assert (await admin.get("/autopilot/preview/" + "x" * 32 + "/")).status_code == 404
    mgr.previews = {k: (v[0], 0) for k, v in mgr.previews.items()}     # links expire
    assert (await admin.get(info["url"])).status_code == 404


async def test_3d_models_assets_and_blend_export_use_the_existing_login(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    tid = (await mgr.create("Build a 3D scene", "vellur-coder"))["id"]
    assert (await admin.get(f"/autopilot/api/agent/tasks/{tid}/models")).status_code in (401, 403)
    data = (await admin.get(f"/autopilot/api/agent/tasks/{tid}/models", headers=ADMIN)).json()
    assert data["blender"] is False and data["models"][0]["path"] == "models/scene.glb"
    assert data["models"][0]["mtime"] == 1.5

    asset = await admin.get(f"/autopilot/api/agent/tasks/{tid}/asset/models/scene.glb", headers=ADMIN)
    assert asset.status_code == 200 and asset.content == GLB
    assert asset.headers["content-type"].startswith("model/gltf-binary")
    assert asset.headers["cache-control"] == "private, no-store" and asset.headers["x-content-type-options"] == "nosniff"
    assert runner.asset_calls[-1] == f"/tasks/{tid}/asset/models/scene.glb"

    assert (await admin.post(f"/autopilot/api/agent/tasks/{tid}/blend-export", headers=ADMIN, json={"path": "scene.blend"})).status_code == 403
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/blend-export", headers=WRITE, json={"path": "scene.blend"})
    assert r.status_code == 200 and r.json()["model"]["path"] == "@derived/scene-abc123.glb"
    assert (await admin.post(f"/autopilot/api/agent/tasks/{tid}/blend-export", headers=WRITE, json={"path": "x.txt"})).status_code == 400


async def test_publish_directly_only_as_fast_forward(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    await admin.put("/autopilot/api/agent/github", headers=WRITE, json={"token": "github_pat_" + "a" * 30})
    tid = (await admin.post("/autopilot/api/agent/tasks", headers=WRITE, json={"prompt": "Add a health endpoint", "repo": "o/r"})).json()["id"]
    await wait_final(mgr, tid)
    assert mgr.task(tid)["base_sha"] == "base1"
    gh.head = "newer2"                                                 # somebody pushed to main meanwhile
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
    assert r.status_code == 409 and r.json()["error"] == "moved"
    assert not any(m == "PATCH" for m, p, _ in gh.calls)
    assert not ctrl.db.kv_get(f"task_rebaseline_failed_{tid}")
    gh.head = "base1"
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
    assert r.status_code == 200 and r.json()["base_sha"] == "commit1" and r.json()["files_changed"] == 0
    patch = [b for m, p, b in gh.calls if m == "PATCH" and p == "/repos/o/r/git/refs/heads/main"][0]
    assert patch == {"sha": "commit1", "force": False} and runner.rebaselined is True
    assert not ctrl.db.kv_get(f"task_rebaseline_failed_{tid}")


async def test_publish_direct_keeps_uncertainty_only_on_unknown_network_errors(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    gh.patch_error = True
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
    assert r.status_code == 502 and r.json()["error"] == "publication_unknown"
    assert ctrl.db.kv_get(f"task_rebaseline_failed_{tid}") is True
    gh.patch_error = False
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
    assert r.status_code == 200 and not ctrl.db.kv_get(f"task_rebaseline_failed_{tid}")


async def test_publish_direct_clears_reconciliation_after_known_refusal(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    gh.patch_status = 422
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish-direct", headers=WRITE)
    assert r.status_code == 502 and r.json()["error"] == "github_rejected"
    assert not ctrl.db.kv_get(f"task_rebaseline_failed_{tid}")


async def test_stop_race_cancels_publication_without_publishing(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Make a change", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    mgr._set(tid, state="publishing", outcome="completed")
    await mgr.cancel(tid)
    await mgr._auto_publish(tid)
    t = mgr.task(tid)
    assert t["state"] == "cancelled" and t["outcome"] == "cancelled"
    assert t["report"]["publication"] == {"status": "skipped", "reason": "publication cancelled"}
    assert not any(m == "PATCH" for m, _, _ in gh.calls)
    assert not any(p.endswith("/pulls") for _, p, _ in gh.calls)


async def test_checkpoints_save_and_restore(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    tid = (await mgr.create("Change the dashboard title", "vellur-coder"))["id"]
    await wait_final(mgr, tid)
    await asyncio.sleep(0.05)
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/checkpoints", headers=WRITE, json={"label": "Good  version"})
    assert r.status_code == 200 and r.json()["checkpoints"][0]["label"] == "Good version"
    cps = (await admin.get(f"/autopilot/api/agent/tasks/{tid}/checkpoints", headers=ADMIN)).json()["checkpoints"]
    assert [c["label"] for c in cps] == ["Good version", "Original version (before the agent's changes)"]
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/restore", headers=WRITE, json={"id": "b" * 40})
    assert r.status_code == 200 and r.json()["task"]["files_changed"] == 1
    kinds = [e["kind"] for e in mgr.events(tid)]
    assert kinds[-2:] == ["checkpoint", "restored"]
    assert (await admin.post(f"/autopilot/api/agent/tasks/{tid}/restore", headers=WRITE, json={"id": "zz"})).status_code == 400
    assert (await admin.post(f"/autopilot/api/agent/tasks/{tid}/checkpoints", json={"label": "x"})).status_code in (401, 403)


async def test_self_repository_and_vision_reach_the_agent(agents):
    ctrl, router, mgr, runner, gh, admin = agents(self_repo="o/r")
    await admin.put("/autopilot/api/agent/github", headers=WRITE, json={"token": "github_pat_" + "a" * 30})
    ctrl.settings.update({"agent_vision": True})
    tid = (await mgr.create("Improve the dashboard", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    assert runner.meta["vision"] is False and runner.meta["auto_screenshots"] is False
    assert "VELLUR AI Core itself" in runner.meta["prompt"] and "explicitly requested AI visual inspection" not in runner.meta["prompt"]
    await asyncio.sleep(0.05)
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/messages", headers=WRITE,
                         json={"text": "Inspect the page", "visual_inspection": True, "auto_screenshots": True})
    assert r.status_code == 200
    await wait_final(mgr, tid)
    assert runner.messages[-1]["vision"] is True and runner.messages[-1]["auto_screenshots"] is True
    assert "explicitly requested AI visual inspection" in runner.messages[-1]["prompt"]
    await asyncio.sleep(0.05)
    await mgr.message(tid, "Continue")
    await wait_final(mgr, tid)
    assert runner.messages[-1]["vision"] is False and runner.messages[-1]["auto_screenshots"] is False
    a = (await admin.get("/autopilot/api/agent", headers=ADMIN)).json()
    assert a["self_repo"] == "o/r" and a["vision"] is True


async def test_visual_inspection_requires_capability_and_is_explicit_on_create(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    url = "/autopilot/api/agent/tasks"
    body = {"prompt": "Inspect the page", "visual_inspection": True}
    ctrl.settings.update({"routes": {"vellur-coder": ["textonly:model"]}})   # a route that declares no image support
    r = await admin.post(url, headers=WRITE, json=body)
    # owner report 2026-11-17: never reject the message (it carries attachments); send it without visual inspection
    assert r.status_code == 200
    tid0 = r.json()["id"]
    await wait_final(mgr, tid0)
    assert runner.meta["vision"] is False and "explicitly requested AI visual inspection" not in runner.meta["prompt"]
    assert any(e["kind"] == "notice" and "visual inspection was skipped" in e["data"].get("text", "") for e in mgr.events(tid0))
    ctrl.settings.update({"agent_vision": True})
    r = await admin.post(url, headers=WRITE, json={**body, "auto_screenshots": True})
    assert r.status_code == 200
    await wait_final(mgr, r.json()["id"])
    assert runner.meta["vision"] is True and runner.meta["auto_screenshots"] is True
    assert "explicitly requested AI visual inspection" in runner.meta["prompt"]


async def test_saved_visual_preferences_apply_when_request_omits_them(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    ctrl.settings.update({"agent_vision": True, "agent_visual_inspection": True, "agent_auto_screenshots": True})
    url = "/autopilot/api/agent/tasks"
    r = await admin.post(url, headers=WRITE, json={"prompt": "Inspect the page"})
    assert r.status_code == 200
    tid = r.json()["id"]
    await wait_final(mgr, tid)
    assert runner.meta["vision"] is True and runner.meta["auto_screenshots"] is True
    await asyncio.sleep(0.05)
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/messages", headers=WRITE, json={"text": "Continue"})
    assert r.status_code == 200
    await wait_final(mgr, tid)
    assert runner.messages[-1]["vision"] is True and runner.messages[-1]["auto_screenshots"] is True
    await asyncio.sleep(0.05)
    r = await admin.post(url, headers=WRITE, json={"prompt": "Inspect the page", "visual_inspection": False, "auto_screenshots": False})
    assert r.status_code == 200
    await wait_final(mgr, r.json()["id"])
    assert runner.meta["vision"] is False and runner.meta["auto_screenshots"] is False


async def test_auto_publication_always_fails_closed_without_immutable_validation(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Make a change", "vellur-coder", "o/r"))["id"]
    t = await wait_final(mgr, tid)
    assert t["report"]["publication"] == {"status": "skipped", "reason": "independent immutable validation is not configured"}
    assert t["files_changed"] == 2 and not any(m == "PATCH" for m, _, _ in gh.calls)


async def test_auto_publication_self_repo_identity_and_opt_in_fail_closed(agents):
    ctrl, router, mgr, runner, gh, admin = agents(self_repo="o/r")
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Make a change", "vellur-coder", "o/r"))["id"]
    t = await wait_final(mgr, tid)
    assert t["report"]["publication"]["reason"] == "VELLUR main requires explicit canonical repository approval"
    ctrl.db.settings_put({"agent_auto_publish_main_repo": "o/r"})
    tid = (await mgr.create("Make another change", "vellur-coder", "o/r"))["id"]
    t = await wait_final(mgr, tid)
    assert t["report"]["publication"]["reason"] == "independent immutable validation is not configured"
    assert not any(m == "PATCH" for m, _, _ in gh.calls)
    gh.canonical_missing = True
    tid = (await mgr.create("Make a third change", "vellur-coder", "o/r"))["id"]
    t = await wait_final(mgr, tid)
    assert t["report"]["publication"]["reason"].startswith("canonical repository identity unavailable")


async def test_auto_publication_self_repo_rename_cannot_bypass_opt_in(agents):
    ctrl, router, mgr, runner, gh, admin = agents(self_repo="old/r")
    mgr.set_github_token("github_pat_" + "a" * 30)
    tid = (await mgr.create("Make a change", "vellur-coder", "o/r"))["id"]
    t = await wait_final(mgr, tid)
    assert t["report"]["publication"]["reason"] == "VELLUR main requires explicit canonical repository approval"
    assert not any(m == "PATCH" for m, _, _ in gh.calls)


async def test_auto_publication_does_not_republish_on_upstream_movement(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    mgr.set_github_token("github_pat_" + "a" * 30)
    gh.head = "newer2"
    tid = (await mgr.create("Make a change", "vellur-coder", "o/r"))["id"]
    t = await wait_final(mgr, tid)
    assert t["report"]["publication"]["status"] == "skipped"
    assert t["files_changed"] == 2 and not any(m == "PATCH" for m, _, _ in gh.calls)


def test_runner_summary_is_fail_closed_and_never_runs_mutable_validation(tmp_path):
    spec = importlib.util.spec_from_file_location("agent_runner_validation", Path(__file__).resolve().parents[2] / "agent" / "runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".vellur").mkdir()
    sentinel = tmp_path / "ran"
    (repo / ".vellur" / "validate.json").write_text(json.dumps({"command": f"touch {sentinel}"}))
    (repo / "data").write_text("old")
    runner.initialize_repo(str(repo))
    (repo / "data").write_text("good")
    result = runner.validation_snapshot({"dir": str(tmp_path)})
    assert result["status"] == "failed" and result["reason"] == "independent immutable validation is not configured"
    assert base64.b64decode(result["files"][0]["content_b64"]) == b"good"
    assert not sentinel.exists()
    (repo / "data").write_text("bad")
    assert runner.validation_snapshot({"dir": str(tmp_path)})["status"] == "failed"


def test_optional_screenshot_notice_is_not_an_error():
    from app.agent import normalize
    kind, data = normalize({"type": "raw", "category": "screenshot_notice", "text": "browser unavailable"})
    assert kind == "notice" and data["text"] == "browser unavailable"


async def test_conversation_work_keeps_context_task_and_followups(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    cid = (await admin.post('/autopilot/api/conversations', headers=WRITE, json={})).json()['id']
    ctrl.db.x("INSERT INTO messages(conv_id,role,content,created) VALUES(?,?,?,?)", (cid, 'user', 'Use accessible controls.', 1))
    r = await admin.post(f'/autopilot/api/conversations/{cid}/work', headers=WRITE,
                         json={'prompt': 'Build a demo landing page', 'repo': 'o/r'})
    assert r.status_code == 200, r.text
    tid = r.json()['task_id']
    await wait_final(mgr, tid)
    assert 'Use accessible controls.' in runner.meta['prompt']
    assert (await admin.get(f'/autopilot/api/conversations/{cid}', headers=ADMIN)).json()['task_id'] == tid
    detail = (await admin.get(f'/autopilot/api/agent/tasks/{tid}', headers=ADMIN)).json()
    assert detail['conversation_id'] == cid
    # Rebuilding the API does not lose the link; it lives in SQLite, not in the browser.
    fresh = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router, agent=mgr)), base_url='https://ai.example')
    try:
        assert (await fresh.get(f'/autopilot/api/conversations/{cid}', headers=ADMIN)).json()['task_id'] == tid
        r = await fresh.post(f'/autopilot/api/conversations/{cid}/work', headers=WRITE, json={'prompt': 'Improve the headings'})
        assert r.status_code == 200 and r.json()['task_id'] == tid
        await wait_final(mgr, tid)
        conv = (await fresh.get(f'/autopilot/api/conversations/{cid}', headers=ADMIN)).json()
        assert [m['content'] for m in conv['messages']][-2:] == ['Build a demo landing page', 'Improve the headings']
        assert mgr.task(tid)['runs'] == 2
        await fresh.delete(f'/autopilot/api/conversations/{cid}', headers=WRITE)
        assert ctrl.db.kv_get(f'conversation_task_{cid}') is None
        assert mgr.task(tid) is not None  # deleting chat must not erase the project
    finally:
        await fresh.aclose()


async def test_conversation_work_guards_missing_image_and_busy_requests(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    cid = (await admin.post('/autopilot/api/conversations', headers=WRITE, json={})).json()['id']
    url = f'/autopilot/api/conversations/{cid}/work'
    body = {'prompt': 'Build a small demo'}
    assert (await admin.post(url, json=body)).status_code == 401
    assert (await admin.post(url, headers=ADMIN, json=body)).status_code == 403
    assert (await admin.post('/autopilot/api/conversations/999/work', headers=WRITE, json=body)).status_code == 404
    assert (await admin.post(url, headers=WRITE, json={**body, 'model': 'vellur-image'})).status_code == 400
    runner.finish_after = 10**9
    tid = (await admin.post(url, headers=WRITE, json=body)).json()['task_id']
    assert (await admin.post(url, headers=WRITE, json=body)).status_code == 409
    assert len((await admin.get(f'/autopilot/api/conversations/{cid}', headers=ADMIN)).json()['messages']) == 1
    await mgr.cancel(tid)
    await wait_final(mgr, tid)


async def test_conversation_work_relinks_when_linked_task_never_started(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    cid = (await admin.post('/autopilot/api/conversations', headers=WRITE, json={})).json()['id']
    url = f'/autopilot/api/conversations/{cid}/work'
    tid = (await admin.post(url, headers=WRITE, json={'prompt': 'Build a small demo'})).json()['task_id']
    await wait_final(mgr, tid)
    ctrl.db.x("UPDATE tasks SET started=NULL WHERE id=?", (tid,))  # as if the runner never opened a session
    r = await admin.post(url, headers=WRITE, json={'prompt': 'Try again'})
    assert r.status_code == 200, r.text
    new = r.json()['task_id']
    assert new != tid and ctrl.db.kv_get(f'conversation_task_{cid}') == new
    await wait_final(mgr, new)


def test_runner_excludes_dependency_folders_even_in_existing_workspaces(tmp_path):
    spec = importlib.util.spec_from_file_location("agent_runner_excl", Path(__file__).resolve().parents[2] / "agent" / "runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x")
    runner.initialize_repo(str(repo))
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text("node_modules/\n")                 # an old workspace with the short list
    runner.ensure_excludes(str(repo))
    text = exclude.read_text()
    assert "ms-playwright/" in text and "venv/" in text and text.count("node_modules/") == 1
    for d in ("ms-playwright/chromium", "venv/lib", "node_modules/x"):
        (repo / d).mkdir(parents=True)
        (repo / d / "f.js").write_text("1")
    (repo / "app.py").write_text("changed")
    runner.git(str(repo), "add", "-A")                    # as the runner does before collecting changes
    assert [f["path"] for f in runner.changed_files(str(repo))] == ["app.py"]


async def test_publish_names_the_failing_operation_and_workflow_permission(agents):
    """Owner report: Create pull request → 403 'Resource not accessible by personal access token' (contents=write)
    although the PAT has Contents + Pull requests write. A fine-grained PAT also needs Workflows write to commit
    files under .github/workflows/; the error must say which operation failed and why, never leak the token."""
    ctrl, router, mgr, runner, gh, admin = agents()
    token = "github_pat_" + "b" * 30
    mgr.set_github_token(token)
    tid = (await mgr.create("Add CI", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    runner.files = [{"path": ".github/workflows/ci.yml", "mode": "100644",
                     "content_b64": base64.b64encode(b"on: push\n").decode()},
                    {"path": "app.py", "mode": "100644", "content_b64": base64.b64encode(b"x=1\n").decode()}]
    gh.trees_forbidden = True
    r = await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)
    j = r.json()
    assert r.status_code == 502 and j["error"] == "github_forbidden", j
    assert "Workflows" in j["message"] and ".github/workflows/ci.yml" in j["message"]
    assert j["details"]["operation"] == "POST /git/trees"
    assert j["details"]["workflow_files"] == [".github/workflows/ci.yml"]
    assert token not in r.text
    assert not [c for c in gh.calls if c[1].endswith("/pulls")]   # no PR attempted after a failed commit
    # Without workflow files the generic message stays, but the failing operation is still named.
    runner.files = None
    j = (await admin.post(f"/autopilot/api/agent/tasks/{tid}/publish", headers=WRITE)).json()
    assert j["error"] == "github_forbidden" and "Workflows" not in j["message"]
    assert j["details"]["operation"] == "POST /git/trees"


async def test_task_detail_explains_why_publishing_is_unavailable(agents):
    ctrl, router, mgr, runner, gh, admin = agents()
    tid = (await mgr.create("Add a health endpoint", "vellur-coder", "o/r"))["id"]
    await wait_final(mgr, tid)
    j = (await admin.get(f"/autopilot/api/agent/tasks/{tid}", headers=ADMIN)).json()
    assert j["can_publish"] is False and "no GitHub token" in j["publish_why"]
    token = "github_pat_" + "c" * 30
    mgr.set_github_token(token)
    r = await admin.get(f"/autopilot/api/agent/tasks/{tid}", headers=ADMIN)
    assert r.json()["can_publish"] is True and r.json()["publish_why"] == "" and token not in r.text
