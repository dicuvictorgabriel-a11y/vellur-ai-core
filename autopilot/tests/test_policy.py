"""NOVA automatic routing: real policies over the owner's model pool, atomic reservations, no capability substitution."""
import asyncio
import json

from test_router import KEY, WRITE, chat, stack  # noqa: F401  (fixture re-export)

CHEAP, DEAR = "deepseek:cheap-coder", "deepseek:dear-coder"


def setup(ctrl, router, **extra):
    router.update_provider("deepseek", {"api_key": "sk-deepseek-secret-1234"})
    ctrl.settings.update({"routes": {"vellur-coder": [DEAR, CHEAP], "vellur-chat": [CHEAP]},
                          "prices": {CHEAP: [0.1, 0.2], DEAR: [5.0, 15.0]}, "monthly_budget_usd": 20.0, **extra})


async def test_without_an_allowance_auto_uses_the_saved_route_and_logs_nothing(stack):
    ctrl, router, up, proxy, _ = stack()
    setup(ctrl, router)
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.status_code == 200 and up.calls[-1][3]["model"] == "dear-coder"     # first saved target, as before NOVA
    assert ctrl.db.one("SELECT COUNT(*) AS n FROM routing_decisions")["n"] == 0
    st = router.policy.status()
    assert st["mode"] == "auto" and st["active"] is False


async def test_modes_are_real_policies(stack):
    ctrl, router, up, proxy, _ = stack()
    setup(ctrl, router, auto_allowance_usd=5.0)
    await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)          # auto: coder → balanced
    assert up.calls[-1][3]["model"] == "cheap-coder"                            # equal (neutral) evidence: value wins
    # quality ignores price: with equal evidence it keeps the owner's order (dear first)
    ctrl.settings.update({"routing_mode": "quality"})
    await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert up.calls[-1][3]["model"] == "dear-coder"
    # recorded outcomes change quality: the cheap model completed tasks, the dear one failed them
    for i, (t, outcome) in enumerate([(CHEAP, "completed")] * 3 + [(DEAR, "failed")] * 3):
        ctrl.db.x("INSERT INTO tasks(id,created,updated,state,repo,base,branch,model,prompt,outcome) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (100 + i, 1e10, 1e10, "done", "", "", "", "vellur-coder", "x", outcome))
        ctrl.db.x("INSERT INTO usage(ts,month,role,target,provider,model,ok,tag) VALUES(?,?,?,?,?,?,?,?)",
                  (1e10, "2286-11", "vellur-coder", t, "deepseek", t.split(":")[1], 1, f"task:{100 + i}"))
    await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert up.calls[-1][3]["model"] == "cheap-coder"
    ctrl.settings.update({"routing_mode": "manual"})
    await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert up.calls[-1][3]["model"] == "dear-coder"


async def test_required_capabilities_are_never_substituted(stack):
    ctrl, router, up, proxy, _ = stack()
    setup(ctrl, router, auto_allowance_usd=5.0)
    body = {**chat(), "messages": [{"role": "user", "content": [{"type": "text", "text": "look"},
                                                                {"type": "image_url", "image_url": {"url": "data:,"}}]}]}
    n = len(up.calls)
    r = await proxy.post("/v1/chat/completions", json=body, headers=KEY)
    assert r.status_code >= 400 and len(up.calls) == n                          # no text-only model was called
    assert "vision" in r.text


async def test_allowance_and_task_limit_are_reserved_atomically(stack):
    ctrl, router, up, proxy, _ = stack()
    setup(ctrl, router, auto_allowance_usd=1.0, routing_mode="quality", auto_task_limit_usd=0.0)
    p = router.policy
    usd = p.expected_usd(DEAR, "vellur-coder", chat())
    assert usd and usd > 0.03
    ids = [p.reserve("chat:1", DEAR, 0.4)[0] for _ in range(3)]                  # concurrent callers
    assert ids[0] and ids[1] and ids[2] is None                                  # 0.4 + 0.4 + 0.4 > 1.0
    assert p.status()["reserved_usd"] == 0.8
    p.release(ids[0]); p.release(ids[1])
    assert p.status()["reserved_usd"] == 0
    ctrl.settings.update({"auto_task_limit_usd": 0.05})
    rid, why = p.reserve("task:7", DEAR, 0.06)
    assert rid is None and "per-task limit" in why
    # a call that does not fit the allowance falls to a model that does, and the reservation is released afterwards
    ctrl.settings.update({"auto_allowance_usd": 0.001, "auto_task_limit_usd": 0.0})
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.status_code == 200 and up.calls[-1][3]["model"] == "cheap-coder"
    assert p.status()["reserved_calls"] == 0


async def test_why_this_model_is_answered_from_the_logged_decision(stack):
    ctrl, router, up, proxy, admin = stack()
    setup(ctrl, router, auto_allowance_usd=5.0, routing_mode="quality")
    await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    d = router.policy.explain(tag="")
    assert d["chosen"] == DEAR and d["policy"] == "quality" and "best observed task success" in d["reason"]
    assert d["cheaper"]["target"] == CHEAP and "no recorded outcomes" in d["cheaper"]["note"]
    assert {x["target"] for x in d["ranked"]} == {CHEAP, DEAR}
    r = await admin.get(f"/autopilot/api/routing/decisions/{d['id']}", headers=WRITE)
    assert r.status_code == 200 and r.json()["chosen"] == DEAR
    s = (await admin.get("/autopilot/api/routing", headers=WRITE)).json()
    assert s["mode"] == "quality" and s["allowance_usd"] == 5.0 and DEAR in s["pool"]
    r = await admin.put("/autopilot/api/routing", headers=WRITE, json={"routing_mode": "turbo"})
    assert r.status_code == 400
    r = await admin.put("/autopilot/api/routing", headers=WRITE, json={"routing_mode": "economy", "auto_allowance_usd": 2})
    assert r.json()["mode"] == "economy" and r.json()["allowance_usd"] == 2.0


async def test_manual_mode_reserves_against_hard_cap_and_tracks_cache_efficiency(stack):
    ctrl, router, up, proxy, admin = stack()
    setup(ctrl, router, routing_mode="manual", total_budget_cap_usd=0.05)
    p = router.policy
    r1, _ = p.reserve("chat:1", DEAR, 0.03, auto=False)
    r2, why = p.reserve("chat:2", DEAR, 0.03, auto=False)
    assert r1 and r2 is None and "budget limit" in why
    p.release(r1)
    # Request whose estimated cost exceeds total_budget_cap_usd falls back or blocks before calling provider
    ctrl.settings.update({"total_budget_cap_usd": 0.01, "routes": {"vellur-coder": [DEAR]}})
    n = len(up.calls)
    res = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert res.status_code == 503 and res.json()["error"]["code"] == "budget" and len(up.calls) == n
    # Record usage with DeepSeek prompt_cache_hit_tokens and verify /autopilot/api/costs efficiency telemetry
    router.record("vellur-coder", CHEAP, router.info(CHEAP), True, 200,
                  {"prompt_tokens": 2000, "completion_tokens": 200, "prompt_cache_hit_tokens": 1500}, 0.0, "chat:1")
    costs = (await admin.get("/autopilot/api/costs", headers=WRITE)).json()
    eff = costs["efficiency"]
    assert eff["in_tokens"] == 2000 and eff["cached_tokens"] == 1500 and eff["cache_hit_rate_pct"] == 75.0
    assert len(eff["enforcement_notes"]) >= 2
