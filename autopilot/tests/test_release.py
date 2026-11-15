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
