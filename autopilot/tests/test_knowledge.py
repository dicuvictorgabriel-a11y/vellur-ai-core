"""NOVA Knowledge: persistent, searchable, scoped, protected, secret-free, retrieved by the agent."""
import httpx

from app.agent import AgentManager
from app.api import build_admin
from app.knowledge import Knowledge, KnowledgeError
from app.router import Router

ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}


def test_search_scope_versions_locks_and_secrets(rig):
    ctrl = rig()
    k = Knowledge(ctrl.db)
    a = k.create({"project": "o/shop", "namespace": "Decisions", "title": "Checkout uses Stripe Elements",
                  "body": "The owner decided: keep Stripe Elements, no custom card form.", "status": "decided"})
    k.create({"project": "o/blog", "namespace": "Design System", "title": "Serif headings", "body": "Headings use a serif."})
    k.create({"namespace": "Constraints", "title": "No localStorage", "body": "Dashboards never use localStorage."})
    assert [r["title"] for r in k.search("stripe checkout", project="o/shop")] == ["Checkout uses Stripe Elements"]
    assert k.search("serif", project="o/shop") == []                     # another project's record stays out
    assert [r["title"] for r in k.search("localstorage", project="o/shop")] == ["No localStorage"]   # workspace-wide
    assert k.search("check", project="o/shop")                            # prefix search
    b = k.update(a["id"], {"body": "Stripe Elements, decided 2026-11-16."}, version=1)
    assert b["version"] == 2
    try:
        k.update(a["id"], {"body": "stale edit"}, version=1)
        raise AssertionError("stale write accepted")
    except KnowledgeError as e:
        assert e.status == 409
    k.lock(a["id"], True)
    for fn in (lambda: k.update(a["id"], {"title": "x"}, version=2), lambda: k.delete(a["id"])):
        try:
            fn()
            raise AssertionError("locked record changed")
        except KnowledgeError as e:
            assert e.code == "locked"
    try:
        k.create({"namespace": "Operations", "title": "deploy", "body": "token ghp_" + "a" * 30})
        raise AssertionError("secret stored")
    except KnowledgeError as e:
        assert e.code == "secret"
    block, ids = k.for_task("Fix the checkout button colour", "o/shop")
    assert a["id"] in ids and "Stripe Elements" in block and "do not ask the owner again" in block
    assert "Serif" not in block


def test_only_verified_runs_become_history(rig):
    ctrl = rig()
    k = Knowledge(ctrl.db)
    task = {"id": 5, "repo": "o/shop", "prompt": "Add a hero section"}
    ok = {"outcome": "completed", "why": "Hero added.", "files": [{"path": "index.html"}], "tests": [{"command": "npm test", "exit": 0}]}
    assert k.record_run(task, 1, {**ok, "outcome": "partial"}) is None
    assert k.record_run(task, 1, {**ok, "tests": []}) is None                       # nothing checked: not a fact
    assert k.record_run(task, 1, {**ok, "tests": [{"command": "npm test", "exit": 1}]}) is None
    r = k.record_run(task, 1, ok)
    assert r["namespace"] == "Implementation history" and r["source"] == "task:5 run 1" and "npm test" in r["body"]
    assert k.record_run(task, 1, ok) is None                                         # idempotent
    k.link_pr(task, "https://github.com/o/shop/pull/3")
    assert "pull/3" in k.get(r["id"])["body"]


async def test_api_and_agent_prompt_use_knowledge(rig):
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    agent = AgentManager(ctrl, router)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router, agent=agent)), base_url="https://ai.example")
    assert (await c.get("/autopilot/api/knowledge")).status_code == 401
    r = await c.post("/autopilot/api/knowledge", headers=WRITE, json={"project": "o/shop", "namespace": "Product",
                                                                     "title": "Audience", "body": "Small bakeries in Romania."})
    assert r.status_code == 200 and r.json()["source"] == "owner"
    kid = r.json()["id"]
    assert (await c.post("/autopilot/api/knowledge", headers=WRITE, json={"namespace": "Nope", "title": "x", "body": "y"})).status_code == 400
    lst = (await c.get("/autopilot/api/knowledge?q=bakeries", headers=ADMIN)).json()
    assert [x["id"] for x in lst["records"]] == [kid] and lst["projects"] == ["o/shop"] and lst["search"] in ("fts5", "like")
    assert (await c.put(f"/autopilot/api/knowledge/{kid}", headers=WRITE, json={"title": "x", "version": 9})).status_code == 409
    assert "Small bakeries" in agent._first_prompt("Write copy for the bakeries landing page", "o/shop")
    assert "Small bakeries" not in agent._first_prompt("Write copy for the bakeries landing page", "o/other")
    assert (await c.delete(f"/autopilot/api/knowledge/{kid}", headers=WRITE)).json() == {"ok": True}
