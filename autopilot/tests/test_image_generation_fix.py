"""Image generation against a mocked OpenRouter: modalities per model, error paths, prices and duplicate requests.

Root cause of the production failure (HTTP 404 "No endpoints found that support the requested output modalities:
image, text" for black-forest-labs/flux.2-klein-4b): every image request asked for ["image", "text"], but FLUX and
Seedream only output images and must be asked for ["image"]. No test here reaches a real provider.
"""
import asyncio
import base64
import json

import httpx
import pytest

from app.agent import AgentManager
from app.api import build_admin
from app.images import aspect_ratio
from app.model_intel import image_modalities, image_prices_from_catalog
from app.router import Router

ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lK3Q6wAAAABJRU5ErkJggg=="
)
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
FLUX = "black-forest-labs/flux.2-klein-4b"
GEMINI = "google/gemini-image"
CATALOG = [
    # Image-only, priced per megapixel: OpenRouter publishes no per-image price for it.
    {"id": FLUX, "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]},
     "pricing": {"prompt": "0", "completion": "0", "image": "0.003"}},
    {"id": GEMINI, "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image", "text"]},
     "pricing": {"prompt": "0.0000003", "completion": "0.0000025", "image_output": "0.039"}},
    {"id": "bytedance-seed/seedream-5-0-flash", "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
     "pricing": {"prompt": "0", "completion": "0", "image_output": "0.00003"}},
]


class OpenRouterMock:
    """Answers like OpenRouter: 404 when the requested output modalities do not match the model."""

    def __init__(self):
        self.calls, self.reply = [], None

    def handler(self, req: httpx.Request):
        body = json.loads(req.content or b"{}") if req.method == "POST" else {}
        self.calls.append((req.url.path, body))
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": CATALOG})
        if self.reply is not None:
            return self.reply(body) if callable(self.reply) else self.reply
        outputs = next(m for m in CATALOG if m["id"] == body["model"])["architecture"]["output_modalities"]
        if sorted(body.get("modalities") or []) != sorted(outputs):
            return httpx.Response(404, json={"error": {"code": 404, "message": "No endpoints found that support the "
                                                       "requested output modalities: " + ", ".join(body.get("modalities") or [])}})
        msg = {"role": "assistant", "content": "Here it is" if "text" in outputs else None,
               "images": [{"type": "image_url", "image_url": {"url": DATA_URL}}]}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"cost": 0.0123}})


@pytest.fixture
def env(rig):
    async def build(target=FLUX, **settings):
        ctrl = rig()
        up = OpenRouterMock()
        router = Router(ctrl, transport=httpx.MockTransport(up.handler))
        router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
        await router.catalog("openrouter")
        ctrl.settings.update({"routes": {"vellur-image": ["openrouter:" + target]}, **settings})
        app = build_admin(ctrl, router=router, agent=AgentManager(ctrl, router))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://ai.example")
        return ctrl, router, up, client
    return build


def gen_calls(up):
    return [b for p, b in up.calls if p.endswith("/chat/completions")]


# ── unit: modalities, prices, sizes ────────────────────────────────────────────────────────────────
def test_modalities_follow_the_catalog_and_image_only_families():
    assert image_modalities(FLUX, ["image"]) == ["image"]
    assert image_modalities(GEMINI, ["image", "text"]) == ["image", "text"]
    assert image_modalities("black-forest-labs/flux.2-pro") == ["image"]          # no catalog: known image-only family
    assert image_modalities("bytedance-seed/seedream-5-0-pro") == ["image"]
    assert image_modalities("openai/gpt-5-image") == ["image", "text"]


def test_input_image_price_is_never_used_as_a_generation_price():
    assert image_prices_from_catalog({"image": "0.003"}) == (None, 0.003)
    assert image_prices_from_catalog({"image_output": "0.039"}) == (0.039, None)
    assert image_prices_from_catalog({"image_output": "0.00003"}) == (None, None)    # a per-token rate, not per image
    assert image_prices_from_catalog(None) == (None, None)


def test_aspect_ratio_from_size():
    assert aspect_ratio("1024x1024") == "1:1"
    assert aspect_ratio("1344x768") == "16:9"
    assert aspect_ratio("832x1248") == "2:3"


# ── request shape ──────────────────────────────────────────────────────────────────────────────────
async def test_flux_is_asked_for_image_only_and_succeeds(env):
    ctrl, router, up, client = await env(FLUX)
    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "a lighthouse", "confirm": True, "size": "1344x768"})
    assert r.status_code == 200, r.text
    (body,) = gen_calls(up)
    assert body["modalities"] == ["image"] and body["model"] == FLUX
    assert body["image_config"] == {"aspect_ratio": "16:9"} and body["usage"] == {"include": True}
    assert "n" not in body
    data = r.json()
    assert data["cost_usd"] == pytest.approx(0.0123) and data["cost_source"] == "provider"
    assert data["images"][0]["mime"] == "image/png"


async def test_mixed_text_and_image_model_keeps_both_modalities(env):
    ctrl, router, up, client = await env(GEMINI)
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "a lighthouse", "confirm": True})
    assert r.status_code == 200, r.text
    assert gen_calls(up)[0]["modalities"] == ["image", "text"]
    assert r.json()["estimate"]["price_per_image"] == pytest.approx(0.039)


async def test_wrong_modalities_without_catalog_are_corrected_once_for_free(env):
    ctrl, router, up, client = await env(FLUX)
    ctrl.db.kv_set("llm_catalog_meta_openrouter", {"models": {}})       # no catalog metadata: name says image-only
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    # Simulate an unknown model the name heuristic gets wrong: the 404 must flip the modalities, once.
    seen = []

    def reply(body):
        seen.append(body["modalities"])
        if body["modalities"] == ["image"]:
            return httpx.Response(404, json={"error": {"message": "No endpoints found that support the requested "
                                                       "output modalities: image"}})
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {"url": DATA_URL}}]}}]})
    up.reply = reply
    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "a lighthouse", "confirm": True})
    assert r.status_code == 200, r.text
    assert seen == [["image"], ["image", "text"]]


async def test_provider_404_for_both_shapes_is_a_clear_unsupported_model_error(env):
    ctrl, router, up, client = await env(FLUX)
    up.reply = httpx.Response(404, json={"error": {"message": "No endpoints found that support the requested output "
                                                   "modalities: image, text"}})
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "x", "confirm": True})
    assert r.status_code == 502 and r.json()["error"] == "unsupported_model"
    assert len(gen_calls(up)) == 2                                      # the original and one corrected attempt
    assert ctrl.db.one("SELECT COUNT(*) AS n FROM generated_images")["n"] == 0
    row = ctrl.db.one("SELECT * FROM usage ORDER BY id DESC LIMIT 1")
    assert row["ok"] == 0 and row["cost_usd"] == 0


@pytest.mark.parametrize("reply,code", [
    (httpx.Response(429, json={"error": {"message": "slow down"}}), "rate_limited"),
    (httpx.Response(402, json={"error": {"message": "no credits"}}), "provider_credit"),
    (httpx.Response(401, json={"error": {"message": "bad key"}}), "auth"),
    (httpx.Response(500, text="oops"), "provider"),
    (httpx.Response(200, text="not json"), "invalid_image"),
    (httpx.Response(200, json={"choices": [{"message": {"content": "sorry, no image"}}]}), "invalid_image"),
    (httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64,!!!"}}]}}]}),
     "invalid_image"),
    (httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {
        "url": "data:image/png;base64," + base64.b64encode(b"<svg>not a png</svg>").decode()}}]}}]}), "invalid_image"),
    (httpx.Response(200, json=["unexpected"]), "invalid_image"),
])
async def test_provider_errors_are_reported_and_nothing_is_saved(env, reply, code):
    ctrl, router, up, client = await env(FLUX)
    up.reply = reply
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "x", "confirm": True})
    assert r.status_code == 502 and r.json()["error"] == code, r.text
    assert ctrl.db.one("SELECT COUNT(*) AS n FROM generated_images")["n"] == 0


async def test_a_charged_answer_without_image_still_counts_as_spending(env):
    ctrl, router, up, client = await env(FLUX)
    up.reply = httpx.Response(200, json={"choices": [{"message": {"content": "no image"}}], "usage": {"cost": 0.02}})
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "x", "confirm": True})
    assert r.status_code == 502
    spent = ctrl.db.one("SELECT SUM(cost_usd) AS c FROM usage")["c"]
    assert spent == pytest.approx(0.02)


# ── prices, confirmation, budget ───────────────────────────────────────────────────────────────────
async def test_unknown_price_always_needs_explicit_confirmation(env):
    ctrl, router, up, client = await env(FLUX, image_confirm_paid=False)
    r = await client.post("/autopilot/api/images/estimate", headers=WRITE, json={"prompt": "x"})
    est = r.json()
    assert est["price_per_image"] is None and est["cost_known"] is False and est["price_source"] == "unknown"
    assert est["requires_confirmation"] is True and est["cost_reported_by_provider"] is True
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "x"})
    assert r.status_code == 409 and r.json()["error"] == "image_confirmation"
    assert "does not publish a price" in r.json()["message"]
    assert gen_calls(up) == []


async def test_known_price_above_the_request_limit_is_refused_before_calling(env):
    ctrl, router, up, client = await env(GEMINI, image_max_usd=0.01)
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "x", "confirm": True})
    assert r.status_code == 400 and r.json()["error"] == "image_budget"
    assert gen_calls(up) == []


async def test_monthly_budget_left_is_checked_before_calling(env):
    ctrl, router, up, client = await env(GEMINI, image_max_usd=5)
    ctrl.settings.update({"monthly_budget_usd": 1})
    now = ctrl.clock()
    from app.budget import month_key
    ctrl.db.x("INSERT INTO usage(ts,month,role,target,provider,model,ok,status,in_tokens,out_tokens,cached_tokens,cost_usd,"
              "cost_source,latency_ms,tag,error,images) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (now, month_key(now), "vellur-chat", "openrouter:x", "openrouter", "x", 1, 200, 0, 0, 0, 0.98, "provider", 1,
               "chat:1", "", 0))
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "x", "confirm": True, "n": 1})
    assert r.status_code == 400 and r.json()["error"] == "image_budget", r.text
    assert gen_calls(up) == []


async def test_exhausted_budget_blocks_paid_images(env):
    ctrl, router, up, client = await env(FLUX)
    ctrl.settings.update({"monthly_budget_usd": 0})
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json={"prompt": "x", "confirm": True})
    assert r.status_code == 400 and r.json()["error"] == "image_unavailable"
    assert gen_calls(up) == []


# ── duplicates ─────────────────────────────────────────────────────────────────────────────────────
async def test_same_request_id_makes_one_paid_call_and_one_message(env):
    ctrl, router, up, client = await env(FLUX)
    conv = (await client.post("/autopilot/api/conversations", headers=WRITE, json={})).json()
    body = {"prompt": "a lighthouse", "confirm": True, "conversation_id": conv["id"], "request_id": "req_abcdef123456"}
    first, second = await asyncio.gather(client.post("/autopilot/api/images/generate", headers=WRITE, json=body),
                                         client.post("/autopilot/api/images/generate", headers=WRITE, json=body))
    third = await client.post("/autopilot/api/images/generate", headers=WRITE, json=body)
    assert first.status_code == second.status_code == third.status_code == 200
    ids = {r.json()["images"][0]["id"] for r in (first, second, third)}
    assert len(ids) == 1 and len(gen_calls(up)) == 1
    saved = (await client.get(f"/autopilot/api/conversations/{conv['id']}", headers=ADMIN)).json()
    assert sum(1 for m in saved["messages"] if (m["meta"] or {}).get("images")) == 1
    assert ctrl.db.one("SELECT COUNT(*) AS n FROM usage WHERE images>0")["n"] == 1


async def test_a_failed_request_id_can_be_retried(env):
    ctrl, router, up, client = await env(FLUX)
    up.reply = httpx.Response(429, json={"error": {"message": "slow down"}})
    body = {"prompt": "x", "confirm": True, "request_id": "req_retry_000001"}
    assert (await client.post("/autopilot/api/images/generate", headers=WRITE, json=body)).status_code == 502
    up.reply = None
    r = await client.post("/autopilot/api/images/generate", headers=WRITE, json=body)
    assert r.status_code == 200 and not r.json().get("duplicate")


async def test_bad_request_id_is_rejected(env):
    ctrl, router, up, client = await env(FLUX)
    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "x", "confirm": True, "request_id": "../../etc"})
    assert r.status_code == 400 and gen_calls(up) == []


async def test_status_lists_verified_image_models_without_prices(env):
    ctrl, router, up, client = await env(FLUX)
    st = (await client.get("/autopilot/api/images/status", headers=ADMIN)).json()
    targets = [m["target"] for m in st["suggested"]]
    assert "openrouter:bytedance-seed/seedream-5-0-flash" in targets
    assert "openrouter:black-forest-labs/flux.2-klein-4b" in targets
    assert all("price" not in m for m in st["suggested"])
    assert st["modalities"] == ["image"]


async def test_agent_cannot_spend_on_an_image_model_with_unknown_price(rig):
    ctrl = rig(agent_runner_secret="r" * 32)
    up = OpenRouterMock()
    router = Router(ctrl, transport=httpx.MockTransport(up.handler))
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    await router.catalog("openrouter")
    ctrl.settings.update({"routes": {"vellur-image": ["openrouter:" + FLUX]}, "agent_image_generation": True})
    app = build_admin(ctrl, router=router, agent=AgentManager(ctrl, router))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://ai.example")
    now = 1_800_000_000.0
    tid = ctrl.db.x("INSERT INTO tasks(created,updated,state,model,prompt) VALUES(?,?,?,?,?)",
                    (now, now, "running", "vellur-coder", "draw")).lastrowid
    r = await client.post("/autopilot/api/agent/images", headers={"X-Runner-Secret": "r" * 32},
                          json={"prompt": "a cube", "task_id": tid})
    assert r.status_code == 402 and r.json()["error"] == "image_price_unknown"
    assert gen_calls(up) == []
    ctrl.settings.update({"image_prices": {"openrouter:" + FLUX: 0.02}})
    r = await client.post("/autopilot/api/agent/images", headers={"X-Runner-Secret": "r" * 32},
                          json={"prompt": "a cube", "task_id": tid})
    assert r.status_code == 200, r.text
    assert gen_calls(up)[0]["modalities"] == ["image"]


async def test_every_suggested_image_model_can_be_saved_as_the_image_route(env):
    from app.model_intel import SUGGESTED_IMAGE_MODELS
    ctrl, router, up, client = await env(FLUX)
    for m in SUGGESTED_IMAGE_MODELS:
        r = await client.put("/autopilot/api/llm/routes", headers=WRITE, json={"vellur-image": [m["target"]]})
        assert r.status_code == 200, r.text
        assert ctrl.settings.get()["routes"]["vellur-image"] == [m["target"]]


async def test_chat_images_are_counted_as_image_generation_in_costs(env):
    ctrl, router, up, client = await env(FLUX)
    conv = (await client.post("/autopilot/api/conversations", headers=WRITE, json={})).json()
    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "x", "confirm": True, "conversation_id": conv["id"]})
    assert r.status_code == 200
    uses = {u["use"]: u for u in (await client.get("/autopilot/api/costs", headers=ADMIN)).json()["by_use"]}
    assert uses["image generation"]["cost_usd"] == pytest.approx(0.0123)
    assert "API clients (OpenCode, apps)" not in uses
