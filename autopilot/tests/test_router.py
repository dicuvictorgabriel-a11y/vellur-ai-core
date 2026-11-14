import json

import httpx
import pytest

from app.api import build_admin
from app.proxy import build_proxy
from app.router import Router

KEY = {"Authorization": "Bearer vllm-test"}
ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}


class Upstream:
    """Fake OpenAI-compatible providers keyed by host."""

    def __init__(self):
        self.calls, self.fail = [], {}

    def handler(self, req: httpx.Request):
        host = req.url.host
        body = json.loads(req.content or b"{}") if req.method == "POST" else {}
        self.calls.append((host, req.url.path, req.headers.get("authorization"), body))
        if host in self.fail:
            f = self.fail[host]
            if f == "down":
                raise httpx.ConnectError("refused")
            return httpx.Response(f, json={"error": {"message": f"upstream {f}"}})
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "deepseek/deepseek-v4.1-flash", "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}, "context_length": 1000000},
                {"id": "some/model:free", "pricing": {"prompt": "0", "completion": "0"}}]})
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        if host == "openrouter.ai":
            usage["cost"] = 0.0042
        if body.get("stream"):
            chunks = [{"choices": [{"delta": {"content": "Hel"}}]}, {"choices": [{"delta": {"content": "lo"}}]},
                      {"choices": [], "usage": usage}]
            text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hello"}}], "usage": usage, "model": body.get("model")})


@pytest.fixture
def stack(rig):
    def build(**kw):
        ctrl = rig(**kw)
        up = Upstream()
        router = Router(ctrl, transport=httpx.MockTransport(up.handler))
        proxy = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_proxy(ctrl, router=router)), base_url="http://p")
        admin = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router)), base_url="https://ai.example")
        return ctrl, router, up, proxy, admin
    return build


def chat(model="vellur-coder", stream=False):
    return {"model": model, "stream": stream, "messages": [{"role": "user", "content": "hi"}]}


async def test_api_route_swaps_model_and_key_and_records_estimated_cost(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("deepseek", {"api_key": "sk-deepseek-secret-1234"})
    ctrl.settings.update({"routes": {"vellur-coder": ["deepseek:deepseek-chat"]}, "prices": {"deepseek:deepseek-chat": [0.15, 0.60]}})
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "Hello"
    host, path, auth, body = up.calls[-1]
    assert (host, path, auth, body["model"]) == ("api.deepseek.com", "/v1/chat/completions", "Bearer sk-deepseek-secret-1234", "deepseek-chat")
    row = ctrl.db.one("SELECT * FROM usage ORDER BY id DESC LIMIT 1")
    assert (row["in_tokens"], row["out_tokens"], row["cost_source"]) == (1000, 500, "estimate")
    assert row["cost_usd"] == pytest.approx((1000 * 0.15 + 500 * 0.60) / 1e6)
    b = ctrl.budget.status(ctrl.settings.get(), None, ctrl.fake_clock.t)
    assert b["api_estimated_usd"] > 0 and b["api_reported_usd"] == 0 and b["confirmed_usd"] == 0


async def test_streaming_passes_through_and_provider_reported_cost(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    ctrl.settings.update({"routes": {"vellur-coder": ["openrouter:deepseek/deepseek-v4.1-flash"]}})
    r = await proxy.post("/v1/chat/completions", json=chat(stream=True), headers=KEY)
    assert r.status_code == 200 and "Hel" in r.text and "[DONE]" in r.text
    body = up.calls[-1][3]
    assert body["stream_options"] == {"include_usage": True} and body["usage"] == {"include": True}
    row = ctrl.db.one("SELECT * FROM usage ORDER BY id DESC LIMIT 1")
    assert (row["cost_usd"], row["cost_source"], row["ok"]) == (0.0042, "provider", 1)
    assert ctrl.budget.status(ctrl.settings.get(), None, ctrl.fake_clock.t)["api_reported_usd"] == 0.0042


async def test_free_models_cost_nothing_and_need_no_price(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    router.update_provider("groq", {"api_key": "gsk_abcdefgh12345678", "plan": "free"})
    ctrl.settings.update({"routes": {"vellur-chat": ["groq:some-model"], "vellur-coder": ["openrouter:some/model:free"]}})
    assert (await proxy.post("/v1/chat/completions", json=chat("vellur-chat"), headers=KEY)).status_code == 200
    assert (await proxy.post("/v1/chat/completions", json=chat("vellur-coder"), headers=KEY)).status_code == 200
    rows = ctrl.db.all("SELECT cost_usd, cost_source FROM usage")
    assert [(r["cost_usd"], r["cost_source"]) for r in rows] == [(0.0, "plan"), (0.0, "plan")]


async def test_paid_fallback_only_when_enabled(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("groq", {"api_key": "gsk_abcdefgh12345678", "plan": "free"})
    router.update_provider("deepseek", {"api_key": "sk-deepseek-secret-1234"})
    ctrl.settings.update({"routes": {"vellur-coder": ["groq:m1", "deepseek:m2"]}, "prices": {"deepseek:m2": [1, 2]}})
    up.fail["api.groq.com"] = 429
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "no_backend"
    assert "rate limited" in r.json()["error"]["message"] and "paid fallback is off" in r.json()["error"]["message"]
    assert not any(c[0] == "api.deepseek.com" for c in up.calls)          # never silently switched to a paid provider
    ctrl.settings.update({"paid_fallback": True})
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.status_code == 200 and r.headers["x-vellur-target"] == "deepseek:m2"
    fails = ctrl.db.all("SELECT target, ok, status FROM usage WHERE ok=0")
    assert {(f["target"], f["status"]) for f in fails} == {("groq:m1", 429)}


async def test_free_fallback_is_automatic(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("groq", {"api_key": "gsk_abcdefgh12345678", "plan": "free"})
    router.update_provider("mistral", {"api_key": "mistral-key-abcdefgh12", "plan": "free"})
    ctrl.settings.update({"routes": {"vellur-coder": ["groq:m1", "mistral:m2"]}})
    up.fail["api.groq.com"] = "down"
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.status_code == 200 and r.headers["x-vellur-target"] == "mistral:m2"


async def test_budget_exhaustion_blocks_paid_but_not_free(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("deepseek", {"api_key": "sk-deepseek-secret-1234"})
    router.update_provider("groq", {"api_key": "gsk_abcdefgh12345678", "plan": "free"})
    ctrl.settings.update({"routes": {"vellur-coder": ["deepseek:m"], "vellur-chat": ["groq:m"]}, "prices": {"deepseek:m": [1, 2]}})
    ctrl.budget.record_provider_charge("mock", "all", 50, ctrl.fake_clock.t)
    r = await proxy.post("/v1/chat/completions", json=chat("vellur-coder"), headers=KEY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "budget" and not up.calls
    assert (await proxy.post("/v1/chat/completions", json=chat("vellur-chat"), headers=KEY)).status_code == 200


async def test_paid_model_without_known_price_is_refused(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("deepseek", {"api_key": "sk-deepseek-secret-1234"})
    ctrl.settings.update({"routes": {"vellur-coder": ["deepseek:unknown-price"]}})
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.json()["error"]["code"] == "config" and "no price known" in r.json()["error"]["message"]


async def test_missing_key_bad_key_and_unknown_model(stack):
    ctrl, router, up, proxy, _ = stack()
    ctrl.settings.update({"routes": {"vellur-coder": ["deepseek:m"]}, "prices": {"deepseek:m": [1, 2]}})
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.json()["error"]["code"] == "config" and "no API key" in r.json()["error"]["message"]
    router.update_provider("deepseek", {"api_key": "sk-deepseek-secret-1234"})
    up.fail["api.deepseek.com"] = 401
    r = await proxy.post("/v1/chat/completions", json=chat(), headers=KEY)
    assert r.json()["error"]["code"] == "auth"
    r = await proxy.post("/v1/chat/completions", json=chat("gpt-99"), headers=KEY)
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"


async def test_empty_routes_fall_back_to_gpu_backend(stack):
    ctrl, router, up, proxy, _ = stack()
    ctrl.settings.update({"auto_start_paid": True})

    seen = {}

    def backend(req):
        seen["auth"], seen["model"] = req.headers["authorization"], json.loads(req.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "gpu"}}]})
    router.client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    r = await proxy.post("/v1/chat/completions", json=chat("vellur-chat"), headers=KEY)
    assert r.status_code == 200 and r.headers["x-vellur-target"] == "gpu"
    assert seen == {"auth": "Bearer vllm-test", "model": "vellur-coder"} and ctrl.current()["state"] == "ready"


async def test_task_tokens_authorize_and_tag_usage(stack):
    ctrl, router, up, proxy, _ = stack()
    router.update_provider("groq", {"api_key": "gsk_abcdefgh12345678", "plan": "free"})
    ctrl.settings.update({"routes": {"vellur-coder": ["groq:m"]}})
    tok = router.issue_token("task:7")
    assert (await proxy.post("/v1/chat/completions", json=chat(), headers={"Authorization": f"Bearer {tok}"})).status_code == 200
    assert ctrl.db.one("SELECT tag FROM usage ORDER BY id DESC LIMIT 1")["tag"] == "task:7"
    router.revoke_tag("task:7")
    assert (await proxy.post("/v1/chat/completions", json=chat(), headers={"Authorization": f"Bearer {tok}"})).status_code == 401
    assert (await proxy.get("/v1/models", headers=KEY)).json()["data"][0]["id"] == "vellur-coder"


async def test_provider_keys_are_write_only_and_catalog_gives_prices(stack):
    ctrl, router, up, proxy, admin = stack()
    r = await admin.put("/autopilot/api/llm/providers/openrouter", headers=WRITE, json={"api_key": "sk-or-v1-supersecret9876"})
    assert r.status_code == 200 and "supersecret" not in r.text
    p = [x for x in r.json()["providers"] if x["name"] == "openrouter"][0]
    assert p["key_set"] is True and p["key_hint"] == "…9876"
    assert "supersecret" not in (await admin.get("/autopilot/api/llm", headers=ADMIN)).text
    assert "supersecret" not in (await admin.get("/autopilot/api/status", headers=ADMIN)).text
    r = await admin.post("/autopilot/api/llm/providers/openrouter/catalog", headers=WRITE)
    assert r.json()["models"][0] == {"id": "deepseek/deepseek-v4.1-flash", "in": pytest.approx(0.15), "out": pytest.approx(0.6), "ctx": 1000000}
    assert router.price("openrouter:deepseek/deepseek-v4.1-flash") == (pytest.approx(0.15), pytest.approx(0.6))
    r = await admin.put("/autopilot/api/llm/routes", headers=WRITE, json={"vellur-coder": ["openrouter:deepseek/deepseek-v4.1-flash"], "vellur-x": []})
    assert r.status_code == 400
    r = await admin.put("/autopilot/api/llm/routes", headers=WRITE, json={"vellur-coder": ["openrouter:deepseek/deepseek-v4.1-flash"]})
    assert r.json()["effective"]["vellur-chat"][0]["usable"] is True          # chat falls back to the coder route
    r = await admin.post("/autopilot/api/llm/test", headers=WRITE, json={"target": "openrouter:deepseek/deepseek-v4.1-flash"})
    assert r.json()["ok"] is True and r.json()["answer"] == "Hello"
    bad = await admin.put("/autopilot/api/llm/providers/my-host", headers=WRITE, json={"base_url": "http://169.254.169.254/x"})
    assert bad.status_code == 400
    ok = await admin.put("/autopilot/api/llm/providers/my-host", headers=WRITE, json={"base_url": "https://llm.example.com/v1", "plan": "subscription", "monthly_usd": 10, "api_key": "abcdefgh12345678"})
    assert ok.status_code == 200
    assert ctrl.budget.status(ctrl.settings.get(), None, ctrl.fake_clock.t)["subscriptions_usd"] == 10


async def test_conversations_persist_and_stream(stack):
    ctrl, router, up, proxy, admin = stack()
    router.update_provider("groq", {"api_key": "gsk_abcdefgh12345678", "plan": "free"})
    ctrl.settings.update({"routes": {"vellur-chat": ["groq:m"]}, "memory": "Answer in Romanian."})
    cid = (await admin.post("/autopilot/api/conversations", headers=WRITE, json={})).json()["id"]
    r = await admin.post(f"/autopilot/api/conversations/{cid}/messages", headers=WRITE, json={"content": "Salut, ce faci?"})
    assert r.status_code == 200 and '"vellur_done": true' in r.text and "Hel" in r.text
    sent = up.calls[-1][3]["messages"]
    assert sent[0] == {"role": "system", "content": "Notes from the owner (always apply):\nAnswer in Romanian."}
    conv = (await admin.get(f"/autopilot/api/conversations/{cid}", headers=ADMIN)).json()
    assert conv["title"] == "Salut, ce faci?" and [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["content"] == "Hello" and conv["messages"][1]["meta"]["target"] == "groq:m"
    await admin.post(f"/autopilot/api/conversations/{cid}/messages", headers=WRITE, json={"content": "Second"})
    assert [m["role"] for m in up.calls[-1][3]["messages"]] == ["system", "user", "assistant", "user"]
    assert ctrl.db.one("SELECT tag FROM usage ORDER BY id DESC LIMIT 1")["tag"] == f"chat:{cid}"
    up.fail["api.groq.com"] = 500
    r = await admin.post(f"/autopilot/api/conversations/{cid}/messages", headers=WRITE, json={"content": "Third"})
    assert "vellur_error" in r.text
    listing = (await admin.get("/autopilot/api/conversations", headers=ADMIN)).json()["conversations"]
    assert listing[0]["id"] == cid
    await admin.delete(f"/autopilot/api/conversations/{cid}", headers=WRITE)
    assert (await admin.get(f"/autopilot/api/conversations/{cid}", headers=ADMIN)).status_code == 404


async def test_costs_report_separates_reported_estimated_and_fixed(stack):
    ctrl, router, up, proxy, admin = stack()
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    router.update_provider("deepseek", {"api_key": "sk-deepseek-secret-1234"})
    ctrl.settings.update({"routes": {"vellur-coder": ["openrouter:deepseek/deepseek-v4.1-flash"], "vellur-max": ["deepseek:m"]},
                          "prices": {"deepseek:m": [1, 2]},
                          "fixed_costs": [{"name": "VPS", "usd": 12, "in_budget": False}, {"name": "Plan", "usd": 10, "in_budget": True}]})
    await proxy.post("/v1/chat/completions", json=chat("vellur-coder"), headers=KEY)
    await proxy.post("/v1/chat/completions", json=chat("vellur-max"), headers=KEY)
    c = (await admin.get("/autopilot/api/costs", headers=ADMIN)).json()
    b = c["budget"]
    assert b["api_reported_usd"] == 0.0042 and b["api_estimated_usd"] == pytest.approx(0.002)
    assert b["fixed_in_budget_usd"] == 10 and b["fixed_outside_budget_usd"] == 12
    assert b["total_usd"] == pytest.approx(10.01, abs=0.005) and b["all_in_total_usd"] == pytest.approx(22.01, abs=0.005)
    assert {r["target"] for r in c["by_target"]} == {"openrouter:deepseek/deepseek-v4.1-flash", "deepseek:m"}
    assert c["by_use"][0]["use"] == "API clients (OpenCode, apps)"
