"""Mission Control: un singur obiectiv activ, un inbox de idei și sarcini legate de rulări reale de agent.
Starea afișată provine mereu din înregistrarea taskului de agent; costurile sunt doar costuri înregistrate (nimic inventat)."""
import asyncio
import json

import httpx
import pytest

from app.agent import FINAL, AgentManager
from app.api import build_admin
from app.router import Router

from test_agent import ADMIN, WRITE, FakeGitHub, FakeRunner, wait_final

M = "/autopilot/api/mission"


@pytest.fixture
def mission(rig):
    def build(upstream=None, **kw):
        ctrl = rig(agent_runner_url="http://agent:7070", agent_runner_secret="r" * 32, **kw)
        up = upstream or (lambda r: httpx.Response(500))
        router = Router(ctrl, transport=httpx.MockTransport(up))
        runner, gh = FakeRunner(), FakeGitHub()
        mgr = AgentManager(ctrl, router, runner_transport=httpx.MockTransport(runner.handler),
                           github_transport=httpx.MockTransport(gh.handler), poll_seconds=0.01)
        admin = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router, agent=mgr)),
                                  base_url="https://ai.example")
        return ctrl, router, mgr, runner, gh, admin
    return build


async def set_objective(admin, title="Pune site-ul pe noul brand", detail="Culori și tipografie"):
    r = await admin.post(M + "/objective", headers=WRITE, json={"title": title, "detail": detail})
    assert r.status_code == 200, r.text
    return r.json()


async def add_item(admin, title, state="backlog", **kw):
    r = await admin.post(M + "/items", headers=WRITE, json={"title": title, "state": state, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def snap(admin):
    r = await admin.get(M, headers=ADMIN)
    assert r.status_code == 200
    return r.json()


async def wait_meta(runner, marker):
    """the agent job reaches the runner asynchronously; wait for the expected task to arrive"""
    for _ in range(500):
        if runner.meta and marker in (runner.meta.get("prompt") or ""):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the runner never received the task with {marker!r}")


async def test_mission_requires_auth_and_starts_empty(mission):
    *_, admin = mission()
    assert (await admin.get(M)).status_code in (401, 403)
    assert (await admin.post(M + "/objective", headers=ADMIN, json={"title": "nou"})).status_code == 403
    d = await snap(admin)
    assert d["objective"] is None and d["items"] == [] and d["next"] is None
    assert set(d["agent"]) == {"available", "why", "running"} and d["agent"]["running"] is None
    assert d["budget"]["objective_estimate_usd"] == 0.0 and d["budget"]["global"]["budget_usd"] == 50.0


async def test_one_active_objective_with_park_and_reactivate(mission):
    *_, admin = mission()
    o1 = await set_objective(admin, "Primul obiectiv")
    o2 = await set_objective(admin, "Al doilea obiectiv")
    d = await snap(admin)
    assert d["objective"]["id"] == o2["id"]
    assert [p["id"] for p in d["parked_objectives"]] == [o1["id"]]
    r = await admin.post(M + "/objective", headers=WRITE, json={"id": o1["id"], "title": "Primul obiectiv"})
    assert r.status_code == 200 and r.json()["state"] == "active"
    d = await snap(admin)
    assert d["objective"]["id"] == o1["id"] and [p["id"] for p in d["parked_objectives"]] == [o2["id"]]
    assert (await admin.post(M + "/objective", headers=WRITE,
                             json={"id": o1["id"], "title": "Primul obiectiv"})).status_code == 400
    assert (await admin.post(M + "/objective", headers=WRITE, json={"title": "ab"})).status_code == 400
    assert (await admin.post(M + "/objective", headers=WRITE, json={"id": 9999, "title": "Nu există"})).status_code == 404
    assert (await admin.post(M + "/objective/park", headers=WRITE, json={})).status_code == 200
    d = await snap(admin)
    assert d["objective"] is None and len(d["parked_objectives"]) == 2
    assert (await admin.post(M + "/objective/park", headers=WRITE, json={})).status_code == 404
    # itemele rămân listate și fără obiectiv activ
    await add_item(admin, "Ideie generală")
    assert len((await snap(admin))["items"]) == 1


async def test_item_states_next_and_dependencies(mission):
    *_, admin = mission()
    await set_objective(admin)
    i1 = await add_item(admin, "Prima din backlog")
    i2 = await add_item(admin, "A doua din backlog")
    d = await snap(admin)
    assert d["next"]["id"] == i1["id"]                                       # backlog, în ordinea poziției
    ir = await add_item(admin, "Pregătită", state="ready")
    d = await snap(admin)
    assert d["next"]["id"] == ir["id"]                                       # ready bate backlog-ul
    assert (await admin.post(M + "/items", headers=WRITE, json={"title": "bad", "state": "verified"})).status_code == 400
    assert (await admin.post(M + "/items", headers=WRITE, json={"title": "bad", "state": "running"})).status_code == 400
    assert (await admin.post(M + "/items", headers=WRITE, json={"title": "bad", "cost_estimate_usd": -1})).status_code == 400
    assert (await admin.post(f"{M}/items/{i1['id']}", headers=WRITE, json={"state": "review"})).status_code == 400
    assert (await admin.post(f"{M}/items/{i1['id']}", headers=WRITE, json={"title": ""})).status_code == 400
    assert (await admin.post(f"{M}/items/{i1['id']}", headers=WRITE, json={})).status_code == 400
    assert (await admin.post(f"{M}/items/9999", headers=WRITE, json={"title": "x"})).status_code == 404
    # o dependență neverificată blochează sarcina din „next”
    idp = await add_item(admin, "Așteaptă cea pregătită", depends_on=ir["id"])
    assert (await admin.post(M + "/items", headers=WRITE, json={"title": "x", "depends_on": 9999})).status_code == 404
    await admin.post(f"{M}/items/{ir['id']}", headers=WRITE, json={"state": "parked"})
    d = await snap(admin)
    assert d["next"]["id"] == i1["id"]                                       # idp e blocată; primul eligibil e i1
    r = await admin.post(f"{M}/items/{idp['id']}", headers=WRITE, json={"plan": "pas 1\npas 2", "state": "idea"})
    assert r.json()["plan"] == "pas 1\npas 2" and r.json()["state"] == "idea"


async def test_start_links_agent_task_and_owner_judges_it(mission):
    ctrl, router, mgr, runner, gh, admin = mission()
    await set_objective(admin)
    it = await add_item(admin, "Adaugă modul întunecat", detail="Comutator în antet", state="ready",
                        plan="Adaugă cheia în settings\nAdaugă comutatorul", cost_estimate_usd=0.5)
    d = await snap(admin)
    assert d["budget"]["objective_estimate_usd"] == pytest.approx(0.5)       # estimarea e estimare, nu cost
    r = await admin.post(f"{M}/items/{it['id']}/start", headers=WRITE, json={})
    assert r.status_code == 200, r.text
    tid = r.json()["agent_task_id"]
    assert r.json()["item"]["effective_state"] == "running"
    await wait_meta(runner, "Sarcină: Adaugă modul întunecat")
    prompt = runner.meta["prompt"]
    assert "Obiectiv: Pune site-ul pe noul brand" in prompt and "Culori și tipografie" in prompt
    assert "Sarcină: Adaugă modul întunecat" in prompt and "Comutator în antet" in prompt
    assert "Plan (urmează acești pași):" in prompt and "Adaugă comutatorul" in prompt
    assert "vellur-coder" in json.dumps(runner.meta)
    d = await snap(admin)
    item = next(x for x in d["items"] if x["id"] == it["id"])
    assert item["state"] == "running" and item["effective_state"] == "running"
    assert item["agent_task"]["id"] == tid and item["agent_task"]["state"] not in FINAL
    # în timp ce agentul lucrează: nu se pornește, nu se editează, nu se judecă
    assert (await admin.post(f"{M}/items/{it['id']}/start", headers=WRITE, json={})).status_code == 400
    assert (await admin.post(f"{M}/items/{it['id']}", headers=WRITE, json={"title": "schimbare"})).status_code == 409
    assert (await admin.post(f"{M}/items/{it['id']}/verify", headers=WRITE)).status_code == 400
    assert (await admin.post(f"{M}/items/{it['id']}/return", headers=WRITE, json={})).status_code == 400
    # rulare încheiată → sarcina intră în review de la sine (derivat din task, nu inventat)
    t = await wait_final(mgr, tid)
    assert t["state"] == "done"
    d = await snap(admin)
    item = next(x for x in d["items"] if x["id"] == it["id"])
    assert item["state"] == "running" and item["effective_state"] == "review"
    assert item["agent_task"]["state"] == "done" and item["agent_task"]["outcome"] == "completed"
    # return în backlog cu notă; obstacolul merge în rularea următoare
    r = await admin.post(f"{M}/items/{it['id']}/return", headers=WRITE, json={"note": "Comutatorul nu e legat de CSS"})
    assert r.json()["state"] == "backlog" and r.json()["blocked_reason"] == "Comutatorul nu e legat de CSS"
    assert (await admin.post(f"{M}/items/{it['id']}/verify", headers=WRITE)).status_code == 400
    r = await admin.post(f"{M}/items/{it['id']}/start", headers=WRITE, json={})
    tid2 = r.json()["agent_task_id"]
    await wait_meta(runner, "Obstacol cunoscut dintr-o rulare anterioară")
    assert "Obstacol cunoscut dintr-o rulare anterioară: Comutatorul nu e legat de CSS" in runner.meta["prompt"]
    assert (await wait_final(mgr, tid2))["state"] == "done"
    # de data asta proprietarul confirmă
    v = await admin.post(f"{M}/items/{it['id']}/verify", headers=WRITE)
    assert v.json()["state"] == "verified" and v.json()["effective_state"] == "verified"
    assert (await admin.post(f"{M}/items/{it['id']}/verify", headers=WRITE)).status_code == 400
    d = await snap(admin)
    assert d["budget"]["objective_estimate_usd"] == 0.0                      # verificată → nu mai contează în estimare
    assert d["budget"]["objective_spent_usd"] == pytest.approx(0.0)          # niciun apel de model în sandbox


async def test_start_refuses_when_busy_or_budget_used_up(mission):
    ctrl, router, mgr, runner, gh, admin = mission()
    await set_objective(admin)
    a = await add_item(admin, "Prima")
    b = await add_item(admin, "A doua")
    runner.finish_after = 10**9                                              # prima rulare nu se termină
    r = await admin.post(f"{M}/items/{a['id']}/start", headers=WRITE, json={})
    assert r.status_code == 200
    tid = r.json()["agent_task_id"]
    r = await admin.post(f"{M}/items/{b['id']}/start", headers=WRITE, json={})
    assert r.status_code == 409 and r.json()["error"] == "busy"
    d = await snap(admin)
    ib = next(x for x in d["items"] if x["id"] == b["id"])
    assert ib["state"] == "backlog" and ib["agent_task_id"] is None          # nu s-a pornit, nimic legat
    assert d["agent"]["running"] is not None and d["agent"]["running"]["id"] == tid
    # oprim prima și epuizăm bugetul: a doua nu pornește
    await admin.post(f"/autopilot/api/agent/tasks/{tid}/cancel", headers=WRITE)
    assert (await wait_final(mgr, tid))["state"] == "cancelled"
    ctrl.budget.record_provider_charge("mock", "all", 50.0, ctrl.clock())
    r = await admin.post(f"{M}/items/{b['id']}/start", headers=WRITE, json={})
    assert r.status_code == 400 and r.json()["error"] == "budget"
    d = await snap(admin)
    ib = next(x for x in d["items"] if x["id"] == b["id"])
    assert ib["state"] == "backlog" and ib["agent_task_id"] is None


async def test_start_validates(mission):
    *_, admin = mission()
    await set_objective(admin)
    i = await add_item(admin, "Ideie")
    assert (await admin.post(f"{M}/items/{i['id']}/start", headers=WRITE, json={"model": "nope"})).status_code == 400
    assert (await admin.post(f"{M}/items/{i['id']}", headers=WRITE, json={"state": "parked"})).status_code == 200
    r = await admin.post(f"{M}/items/{i['id']}/start", headers=WRITE, json={})
    assert r.status_code == 400 and "deschide-o în Projects" in r.json()["message"]
    assert (await admin.post(f"{M}/items/9999/start", headers=WRITE, json={})).status_code == 404


async def test_suggest_plan_is_explicit_and_costs_are_recorded(mission):
    class Up:
        def __init__(self):
            self.status, self.calls = 200, []

        def handler(self, req: httpx.Request):
            self.calls.append(json.loads(req.content))
            if self.status != 200:
                return httpx.Response(500, json={"error": {"message": "providerul e în jos"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Adaug cheia în settings\nAdaug comutatorul în antet"}}],
                                             "usage": {"prompt_tokens": 9, "completion_tokens": 3, "cost": 0.002}})
    up = Up()
    ctrl, router, mgr, runner, gh, admin = mission(upstream=up.handler)
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    ctrl.settings.update({"routes": {"vellur-chat": ["openrouter:some/model"]}})
    await set_objective(admin)
    it = await add_item(admin, "Adaugă modul întunecat", detail="Comutator în antet")
    r = await admin.post(f"{M}/items/{it['id']}/suggest-plan", headers=WRITE)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "Adaug cheia în settings" in j["plan"]
    assert j["cost_usd"] == pytest.approx(0.002) and j["cost_source"] == "provider"
    d = await snap(admin)
    assert next(x for x in d["items"] if x["id"] == it["id"])["plan"].startswith("Adaug cheia")
    body = up.calls[-1]
    assert body["model"] == "some/model" and body["stream"] is False        # un singur apel explicit, cu cost înregistrat
    user = body["messages"][-1]["content"]
    assert "Obiectiv: Pune site-ul pe noul brand" in user and "Sarcină: Adaugă modul întunecat" in user
    up.status = 500
    r = await admin.post(f"{M}/items/{it['id']}/suggest-plan", headers=WRITE)
    assert r.status_code == 502 and r.json()["error"] == "model"
    assert "providerul e în jos" in r.json()["message"]
    assert (await admin.post(f"{M}/items/9999/suggest-plan", headers=WRITE)).status_code == 404
