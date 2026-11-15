"""Regression tests for the Autopilot freeze (15.11.2026): GET /autopilot/api/llm recomputed the whole budget for
every catalog model, and each budget computation ran one query per unreconciled HyperAI session (quadratic).
With a few hundred HyperAI sessions and an OpenRouter catalog the request took minutes, on the event loop, so
/healthz stopped answering."""
import asyncio
import threading
import time

import httpx

from app.api import build_admin
from app.router import Router

ADMIN = {"X-Vellur-Admin": "s" * 32}
SESSIONS, MODELS = 600, 400


def seed(ctrl, sessions=SESSIONS, models=MODELS):
    now = ctrl.clock()
    for i in range(sessions):          # HyperAI has no billing API: its sessions stay unreconciled all month
        ctrl.db.x("INSERT INTO sessions(provider,profile,trigger,state,created_at,billing_started_at,ended_at,dry_run,reconciled) "
                  "VALUES('hyperai','p','request','ended',?,?,?,0,0)", (now - 7200 + i, now - 7200 + i, now - 7100 + i))
    ctrl.db.kv_set("llm_catalog_openrouter", {"models": [{"id": f"vendor/model-{i}", "in": 1.0, "out": 2.0} for i in range(models)]})
    ctrl.db.kv_set("llm_keys", {"openrouter": "sk-or-" + "x" * 40})


def app_for(ctrl):
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    return router, httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router)), base_url="https://ai.example")


def count_queries(db):
    calls = {"n": 0}
    real = db.all

    def counted(sql, args=()):
        calls["n"] += 1
        return real(sql, args)
    db.all = counted
    return calls


async def test_budget_status_runs_a_constant_number_of_queries(rig):
    ctrl = rig()
    seed(ctrl, models=0)
    calls = count_queries(ctrl.db)
    t = time.perf_counter()
    st = ctrl.budget.status(ctrl.settings.get(), None, ctrl.clock())
    assert time.perf_counter() - t < 0.1
    assert calls["n"] <= 5, f"{calls['n']} queries for one budget status"
    assert st["hyperai_hours_used_estimate"] > 0


async def test_hyperai_estimate_unchanged_by_the_optimisation(rig):
    """Same numbers as the original per-session computation: included hours first, then the hourly rate."""
    ctrl = rig()
    ctrl.settings.update({"hyperai_included_hours": 3, "hyperai_hourly_after_usd": 0.5})
    now = ctrl.clock()
    for i in range(6):                 # six sessions of one hour each
        ctrl.db.x("INSERT INTO sessions(provider,profile,trigger,state,created_at,billing_started_at,ended_at,dry_run,reconciled) "
                  "VALUES('hyperai','p','request','ended',?,?,?,0,0)", (now - 30000 + i * 4000, now - 30000 + i * 4000, now - 30000 + i * 4000 + 3600))
    st = ctrl.budget.status(ctrl.settings.get(), None, now)
    assert st["hyperai_hours_used_estimate"] == 6.0
    assert st["estimated_unconfirmed_usd"] == 1.5          # 3 included hours, 3 hours × $0.50
    rows = ctrl.db.all("SELECT * FROM sessions ORDER BY id")
    assert [round(ctrl.budget.session_estimate(r, ctrl.settings.get(), now), 2) for r in rows] == [0, 0, 0, 0.5, 0.5, 0.5]


async def test_model_list_computes_the_budget_once(rig):
    ctrl = rig()
    seed(ctrl)
    router, c = app_for(ctrl)
    n = {"status": 0}
    real = ctrl.budget.status

    def counted(*a, **k):
        n["status"] += 1
        return real(*a, **k)
    ctrl.budget.status = counted
    t = time.perf_counter()
    r = await c.get("/autopilot/api/llm", headers=ADMIN)
    elapsed = time.perf_counter() - t
    assert r.status_code == 200
    assert n["status"] == 1, f"budget computed {n['status']} times for one model list"
    assert elapsed < 2.0, f"GET /autopilot/api/llm took {elapsed:.2f}s with {MODELS} models and {SESSIONS} sessions"
    m = await c.get("/autopilot/api/llm/models", headers=ADMIN)
    assert m.status_code == 200 and len(m.json()["models"]) == MODELS + 1 and n["status"] == 2


async def test_repeated_model_list_requests_stay_fast(rig):
    ctrl = rig()
    seed(ctrl)
    _, c = app_for(ctrl)
    t = time.perf_counter()
    for _ in range(10):
        assert (await c.get("/autopilot/api/llm", headers=ADMIN)).status_code == 200
    assert time.perf_counter() - t < 8.0


async def test_healthz_answers_while_model_lists_are_computed(rig):
    """The heavy endpoint runs in a worker thread: health checks keep answering within Docker's 5 s timeout.

    1. Deterministic: the heavy catalog work never runs on the event-loop thread.
    2. The event loop is never blocked near the ~4.5 s a regression causes (three 1.5 s computations run on the loop
       one after another); a sleeping ticker measures the longest stall.
    3. /healthz keeps answering: median probe fast, worst probe inside Docker's 4 s urlopen / 5 s timeout.
    The bounds are 3 s, not 1 s: /healthz reads the database on the loop, and a worker holding the DB lock while
    a CPU-bound thread hogs the GIL can delay it by up to one heavy computation (~1.5 s here; sqlite3 takes the
    GIL back for every row it fetches). That bounded wait is not the regression this test guards against, and a
    1 s bound made the test fail about once in six full-suite runs."""
    ctrl = rig()
    seed(ctrl)
    router, c = app_for(ctrl)
    real = router.models
    loop_thread = threading.get_ident()
    seen = {"threads": [], "active": 0, "done": 0}

    def slow_models(*a, **k):          # make the catalog work deliberately slow and CPU/GIL-bound
        seen["threads"].append(threading.get_ident())
        seen["active"] += 1
        try:
            end = time.perf_counter() + 1.5
            while time.perf_counter() < end:
                sum(range(1000))
            return real(*a, **k)
        finally:
            seen["active"] -= 1
            seen["done"] += 1
    router.models = slow_models

    lag = {"worst": 0.0, "stop": False}

    async def ticker():                # how late does a 10 ms sleep wake up? = longest stretch the loop was blocked
        while not lag["stop"]:
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            lag["worst"] = max(lag["worst"], time.perf_counter() - t - 0.01)
    tick = asyncio.create_task(ticker())

    heavy = [asyncio.create_task(c.get("/autopilot/api/llm", headers=ADMIN)) for _ in range(3)]
    await asyncio.sleep(0.05)
    probes, overlapped = [], 0
    for _ in range(10):
        overlapped += seen["active"] > 0
        t = time.perf_counter()
        r = await c.get("/healthz")
        probes.append(time.perf_counter() - t)
        assert r.status_code == 200 and r.json()["ok"] is True
        await asyncio.sleep(0.05)
    results = await asyncio.gather(*heavy)
    lag["stop"] = True
    await tick

    assert all(r.status_code == 200 for r in results)
    assert loop_thread not in seen["threads"], "the heavy model-list work ran on the event-loop thread"
    assert lag["worst"] < 3.0, f"the event loop was blocked for {lag['worst']:.2f}s while model lists were computed"
    assert seen["done"] >= 3 and overlapped > 0, "the health probes did not overlap the heavy work"
    probes.sort()
    median = (probes[4] + probes[5]) / 2
    assert median < 0.5, f"/healthz median {median:.2f}s while model lists were computed"
    assert probes[-1] < 3.0, f"/healthz took {probes[-1]:.2f}s while model lists were computed (Docker allows 4-5 s)"


def provider_catalog(n):
    return {"data": [{"id": f"vendor{i % 40}/model-{i}" + (":free" if i % 7 == 0 else ""), "context_length": 128000,
                      "pricing": {"prompt": "0.000001", "completion": "0.000002", "image": "0.001" if i % 9 == 0 else "0"},
                      "architecture": {"input_modalities": ["text", "image"] if i % 3 == 0 else ["text"],
                                       "output_modalities": ["text", "image"] if i % 9 == 0 else ["text"]},
                      "supported_parameters": ["tools"] if i % 2 else [], "description": "x" * 400} for i in range(n)]}


async def test_refresh_then_model_list_with_large_real_world_catalogs(rig):
    """Second freeze (15.11.2026): after "Refresh list" stored real catalogs, every catalog model decoded the whole
    stored catalog several times (thousands of JSON decodes per request): seconds per request, minutes on the VPS."""
    ctrl = rig()
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=provider_catalog(700))))
    ctrl.db.kv_set("llm_keys", {"openrouter": "sk-or-" + "x" * 40, "deepseek": "sk-" + "y" * 30})
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router)), base_url="https://ai.example")
    write = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}
    for name in ("openrouter", "deepseek"):
        assert (await c.post(f"/autopilot/api/llm/providers/{name}/catalog", headers=write, json={})).status_code == 200
    decodes = {"n": 0}
    import json as _json
    real_loads = _json.loads

    def counting_loads(s, *a, **k):
        if isinstance(s, (str, bytes)) and len(s) > 20000:      # a stored catalog, not small settings values
            decodes["n"] += 1
        return real_loads(s, *a, **k)
    _json.loads = counting_loads
    try:
        t = time.perf_counter()
        r1, r2 = await asyncio.gather(c.get("/autopilot/api/llm", headers=ADMIN), c.get("/autopilot/api/llm/models", headers=ADMIN))
        elapsed = time.perf_counter() - t
    finally:
        _json.loads = real_loads
    assert r1.status_code == r2.status_code == 200 and len(r2.json()["models"]) == 1401
    assert elapsed < 1.5, f"model list with 1400 catalog models took {elapsed:.2f}s"
    assert decodes["n"] <= 8, f"{decodes['n']} catalog decodes for one page load"


async def test_shared_kv_reads_see_every_write(rig):
    ctrl = rig()
    ctrl.db.kv_set("llm_catalog_openrouter", {"models": [{"id": "a"}]})
    assert ctrl.db.kv_get_shared("llm_catalog_openrouter")["models"][0]["id"] == "a"
    ctrl.db.kv_set("llm_catalog_openrouter", {"models": [{"id": "b"}]})
    assert ctrl.db.kv_get_shared("llm_catalog_openrouter")["models"][0]["id"] == "b"
    assert ctrl.db.kv_get_shared("missing", {"x": 1}) == {"x": 1}
    ctrl.db.kv_set("missing", {"y": 2})
    assert ctrl.db.kv_get_shared("missing", {}) == {"y": 2}
