import httpx

from app.api import build_admin

ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}
SHA, OLD = "a" * 40, "b" * 40


def github(ci="success", status="completed"):
    def handler(req: httpx.Request):
        p = req.url.path
        if p.endswith("/check-runs"):
            return httpx.Response(200, json={"check_runs": [{"status": status, "conclusion": ci}]})
        if p.endswith("/commits/main") or p.endswith("/commits/" + SHA):
            return httpx.Response(200, json={"sha": SHA, "commit": {"message": "Add a badge\n\nbody", "committer": {"date": "2026-11-15T10:00:00Z"}}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def client(rig, transport):
    from app.agent import AgentManager
    from app.router import Router
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    agent = AgentManager(ctrl, router, github_transport=transport)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router, agent=agent)), base_url="https://ai.example")


async def test_release_is_installed_only_after_the_owner_approves_an_exact_commit(rig):
    c = await client(rig, github())
    assert (await c.get("/autopilot/api/release")).status_code == 401
    o = (await c.get("/autopilot/api/release", headers=ADMIN)).json()
    assert o["latest"] == {"sha": SHA, "message": "Add a badge", "date": "2026-11-15T10:00:00Z", "ci": "passed"}
    assert o["approved"] == "" and o["installer_active"] is False
    assert (await c.get("/autopilot/api/release/pending", headers=ADMIN)).json() == {"approved": "", "approved_at": 0, "request": ""}   # nothing by itself
    assert (await c.post("/autopilot/api/release/approve", json={"sha": SHA})).status_code in (401, 403)
    assert (await c.post("/autopilot/api/release/approve", headers=WRITE, json={"sha": "main"})).status_code == 400
    assert (await c.post("/autopilot/api/release/approve", headers=WRITE, json={"sha": OLD})).status_code == 404   # not in the repository
    assert (await c.post("/autopilot/api/release/approve", headers=WRITE, json={"sha": SHA})).json()["approved"] == SHA
    p = (await c.get("/autopilot/api/release/pending", headers=ADMIN)).json()
    assert p["approved"] == SHA and p["approved_at"] > 0 and p["request"] == ""
    r = await c.post("/autopilot/api/release/report", headers=WRITE, json={"installed": SHA, "previous": OLD, "last": "installed aaaaaaa", "clear": "approved"})
    assert r.json()["approved"] == "" and r.json()["installed"] == SHA
    o = (await c.get("/autopilot/api/release", headers=ADMIN)).json()
    assert o["installer_active"] is True and o["previous"] == OLD and o["last"] == "installed aaaaaaa"
    assert (await c.post("/autopilot/api/release/rollback", headers=WRITE)).json()["request"] == "rollback"
    assert (await c.get("/autopilot/api/release/pending", headers=ADMIN)).json()["request"] == "rollback"
    await c.post("/autopilot/api/release/report", headers=WRITE, json={"installed": "", "previous": "", "last": "rolled back", "clear": "rollback"})
    p = (await c.get("/autopilot/api/release/pending", headers=ADMIN)).json()
    assert p["approved"] == "" and p["request"] == "" and "approved_at" in p


async def test_release_overview_reports_failed_and_running_tests(rig):
    assert (await (await client(rig, github(ci="failure"))).get("/autopilot/api/release", headers=ADMIN)).json()["latest"]["ci"] == "failed"
    assert (await (await client(rig, github(status="in_progress"))).get("/autopilot/api/release", headers=ADMIN)).json()["latest"]["ci"] == "pending"


async def test_delivery_reports_only_what_github_and_the_installer_confirm(rig):
    from app.release import Release
    ctrl = rig()
    MERGE, state = "c" * 40, {"pr": {"state": "open", "merged": False, "draft": True, "title": "T"}, "reviews": [], "compare": "behind", "down": False}

    def handler(req: httpx.Request):
        p = req.url.path
        if state["down"]:
            return httpx.Response(500)
        if p.endswith("/pulls/7/reviews"):
            return httpx.Response(200, json=state["reviews"])
        if p.endswith("/pulls/7"):
            return httpx.Response(200, json=state["pr"])
        if "/compare/" in p:
            return httpx.Response(200, json={"status": state["compare"]})
        return httpx.Response(404)
    rel = Release(ctrl.db, "o/self", lambda: "", httpx.MockTransport(handler))
    st = lambda d: {x["key"]: x["status"] for x in d["stages"]}
    task = {"repo": "o/self", "base": "main", "branch": "", "pr_url": "", "files_changed": 0, "report": {}}
    assert st(await rel.delivery(task)) == {"saved": "pending", "pr": "pending", "review": "pending", "merged": "pending", "deployed": "pending"}
    task.update(files_changed=2, pr_url="https://github.com/o/self/pull/7")
    d = await rel.delivery(task)
    assert st(d) == {"saved": "done", "pr": "done", "review": "pending", "merged": "pending", "deployed": "pending"} and d["pr"]["draft"]
    state["reviews"] = [{"state": "APPROVED"}]
    assert st(await rel.delivery(task))["review"] == "done"
    state["pr"] = {"state": "closed", "merged": True, "merge_commit_sha": MERGE}
    d = await rel.delivery(task)   # merged, but the installer never reported a version: deployment is unknown, not done
    assert st(d)["merged"] == "done" and st(d)["deployed"] == "unknown" and d["errors"]
    rel.report(OLD, "", "", "")
    assert st(await rel.delivery(task))["deployed"] == "pending"   # main still runs an older commit
    state["compare"] = "ahead"
    assert st(await rel.delivery(task))["deployed"] == "done"
    state["down"] = True   # GitHub unreachable: nothing is claimed
    d = await rel.delivery(task)
    assert st(d) == {"saved": "done", "pr": "unknown", "review": "unknown", "merged": "unknown", "deployed": "pending"} and d["errors"]
    state["down"] = False
    assert st(await rel.delivery({**task, "repo": "o/other"}))["deployed"] == "untracked"
    direct = {"repo": "o/self", "base": "main", "pr_url": "", "files_changed": 1, "report": {"publication": {"status": "published", "branch": "main", "commit": OLD}}}
    assert st(await rel.delivery(direct)) == {"saved": "done", "pr": "skipped", "review": "skipped", "merged": "done", "deployed": "done"}
    c = await client(rig, httpx.MockTransport(handler))
    assert (await c.get("/autopilot/api/agent/tasks/1/delivery")).status_code == 401
    assert (await c.get("/autopilot/api/agent/tasks/1/delivery", headers=ADMIN)).status_code == 404


def test_task_cost_keeps_confirmed_estimated_and_unknown_apart(rig):
    from app.router import Router
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    for src, cost in (("provider", 0.02), ("estimate", 0.005), ("unpriced", 0.0), ("plan", 0.0), ("provider", 0.01)):
        ctrl.db.x("INSERT INTO usage(ts,month,role,target,provider,model,ok,cost_usd,cost_source,tag) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (1, "2026-11", "vellur-coder", "p:m", "p", "m", 1, cost, src, "task:9"))
    c = router.tag_costs("task:9")
    assert c == {"confirmed_usd": 0.03, "estimated_usd": 0.005, "free_requests": 1, "unpriced_requests": 1, "requests": 5}
    assert router.tag_costs("task:404")["requests"] == 0
