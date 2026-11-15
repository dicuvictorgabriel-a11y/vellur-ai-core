import base64
import json

import httpx
import pytest

from app.agent import AgentManager
from app.api import build_admin
from app.images import wants_image
from app.router import Router

ADMIN = {"X-Vellur-Admin": "s" * 32}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lK3Q6wAAAABJRU5ErkJggg=="
)
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


class ImageUpstream:
    def __init__(self):
        self.calls = []

    def handler(self, req: httpx.Request):
        body = json.loads(req.content or b"{}") if req.method == "POST" else {}
        self.calls.append((req.url.host, req.url.path, body))
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "text/model", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                 "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 32000,
                 "supported_parameters": ["tools", "reasoning"]},
                {"id": "vision/model", "architecture": {"input_modalities": ["text", "image"],
                                                        "output_modalities": ["text"]},
                 "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
                {"id": "img/model", "architecture": {"input_modalities": ["text"],
                                                     "output_modalities": ["image", "text"]},
                 "pricing": {"prompt": "0", "completion": "0"}},
            ]})
        if req.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {
                "content": "Here is the image", "images": [{"image_url": {"url": DATA_URL}}]}}]})
        if req.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        return httpx.Response(404)


@pytest.fixture
def images(rig):
    def build():
        ctrl = rig()
        up = ImageUpstream()
        router = Router(ctrl, transport=httpx.MockTransport(up.handler))
        router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
        router.update_provider("imgapi", {"api_key": "sk-img-abcdefgh1234", "base_url": "https://img.example/v1",
                                          "image_api": "images"})
        agent = AgentManager(ctrl, router)
        app = build_admin(ctrl, router=router, agent=agent)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://ai.example")
        return ctrl, router, up, client
    return build


def test_image_intent_accepts_direct_draw_but_not_abstract_draw():
    assert wants_image("draw a red cube")
    assert wants_image("draw me a small fox")
    assert not wants_image("draw a conclusion")


async def test_discovery_keeps_capabilities_separate_from_catalog_shape(images):
    ctrl, router, up, client = images()
    r = await client.post("/autopilot/api/llm/providers/openrouter/catalog", headers=WRITE)
    assert r.status_code == 200
    assert r.json()["models"][0] == {"id": "text/model", "in": pytest.approx(1), "out": pytest.approx(2), "ctx": 32000}
    rows = (await client.get("/autopilot/api/llm/models", headers=ADMIN)).json()
    by_id = {m["target"]: m for m in rows["models"]}
    assert "tool_call" in by_id["openrouter:text/model"]["capabilities"]
    assert "reasoning" in by_id["openrouter:text/model"]["capabilities"]
    assert by_id["openrouter:vision/model"]["vision"] is True
    assert by_id["openrouter:vision/model"]["image_generation"] is False
    assert by_id["openrouter:img/model"]["image_generation"] is True
    assert by_id["openrouter:img/model"]["image_price"] is None
    assert router.capabilities("openrouter:not-in-catalog") == ["text_input", "text_output", "chat"]
    assert router.chain("vellur-chat", capability="vision") == []


async def test_vision_capability_filters_the_route_chain(images):
    ctrl, router, up, client = images()
    await client.post("/autopilot/api/llm/providers/openrouter/catalog", headers=WRITE)
    ctrl.settings.update({"routes": {"vellur-chat": ["openrouter:text/model", "openrouter:vision/model"]},
                          "prices": {"openrouter:text/model": [1, 2], "openrouter:vision/model": [1, 2]}})
    assert router.chain("vellur-chat", capability="vision") == ["openrouter:vision/model"]
    assert router.can_vision("vellur-chat") is True
    state = (await client.get("/autopilot/api/llm", headers=ADMIN)).json()
    assert state["capabilities"]["vision"] >= 1


async def test_image_generation_requires_confirmation_and_is_saved(images):
    ctrl, router, up, client = images()
    await client.post("/autopilot/api/llm/providers/openrouter/catalog", headers=WRITE)
    ctrl.settings.update({"routes": {"vellur-image": ["openrouter:img/model"]},
                          "image_prices": {"openrouter:img/model": 0.04}, "image_max_usd": 1.0})
    r = await client.post("/autopilot/api/images/estimate", headers=WRITE,
                          json={"prompt": "a red cube", "model": "vellur-image"})
    assert r.status_code == 200
    est = r.json()
    assert est["target"] == "openrouter:img/model" and est["estimated_usd"] == pytest.approx(0.04)
    assert est["requires_confirmation"] is True
    state = (await client.get("/autopilot/api/llm", headers=ADMIN)).json()
    assert state["effective"]["vellur-image"][0]["usable"] is True
    rows = (await client.get("/autopilot/api/llm/models", headers=ADMIN)).json()["models"]
    assert next(m for m in rows if m["target"] == "openrouter:img/model")["usable"] is True

    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "a red cube", "model": "vellur-image", "confirm": False})
    assert r.status_code == 409 and r.json()["error"] == "image_confirmation"
    assert not any(c[1].endswith("/chat/completions") for c in up.calls)

    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "a red cube", "model": "vellur-image", "confirm": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["cost_usd"] == pytest.approx(0.04) and data["cost_source"] == "estimate"
    image = data["images"][0]
    assert image["mime"] == "image/png" and image["width"] == 1 and image["height"] == 1
    content = await client.get(image["url"], headers=ADMIN)
    assert content.status_code == 200 and content.content == PNG
    assert content.headers["content-type"].startswith("image/png")

    row = ctrl.db.one("SELECT * FROM usage ORDER BY id DESC LIMIT 1")
    assert row["images"] == 1 and row["cost_usd"] == pytest.approx(0.04)
    assert ctrl.db.one("SELECT COUNT(*) AS n FROM generated_images")["n"] == 1


async def test_openai_images_endpoint_and_missing_price_is_clear(images):
    ctrl, router, up, client = images()
    await client.post("/autopilot/api/llm/providers/imgapi/catalog", headers=WRITE)
    ctrl.settings.update({"routes": {"vellur-image": ["imgapi:img/model"]}})
    r = await client.post("/autopilot/api/images/estimate", headers=WRITE, json={"prompt": "a cat"})
    assert r.status_code == 400 and r.json()["error"] == "image_unavailable"

    ctrl.settings.update({"image_prices": {"imgapi:img/model": 0.02}})
    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "a cat", "model": "vellur-image", "confirm": True, "n": 1})
    assert r.status_code == 200, r.text
    call = [c for c in up.calls if c[1].endswith("/images/generations")][-1]
    assert call[2]["model"] == "img/model" and call[2]["response_format"] == "b64_json"
    assert r.json()["images"][0]["mime"] == "image/png"


async def test_chat_image_intent_asks_then_returns_a_real_image(images):
    ctrl, router, up, client = images()
    await client.post("/autopilot/api/llm/providers/openrouter/catalog", headers=WRITE)
    ctrl.settings.update({"routes": {"vellur-image": ["openrouter:img/model"]},
                          "image_prices": {"openrouter:img/model": 0.04}})
    conv = (await client.post("/autopilot/api/conversations", headers=WRITE,
                              json={"model": "vellur-chat"})).json()
    r = await client.post(f"/autopilot/api/conversations/{conv['id']}/messages", headers=WRITE,
                          json={"content": "draw a red cube", "model": "vellur-chat"})
    assert r.status_code == 200 and "vellur_image_confirm" in r.text
    assert not any(c[1].endswith("/chat/completions") for c in up.calls)

    r = await client.post("/autopilot/api/images/generate", headers=WRITE,
                          json={"prompt": "generate an image of a red cube", "model": "vellur-image",
                                "confirm": True, "conversation_id": conv["id"]})
    assert r.status_code == 200, r.text
    saved = (await client.get(f"/autopilot/api/conversations/{conv['id']}", headers=ADMIN)).json()
    assistant = [m for m in saved["messages"] if m["role"] == "assistant"][-1]
    assert assistant["meta"]["images"][0]["id"] == r.json()["images"][0]["id"]


async def test_agent_image_endpoint_requires_the_runner_secret_and_a_real_task(images, rig):
    ctrl = rig(agent_runner_secret="r" * 32)
    up = ImageUpstream()
    router = Router(ctrl, transport=httpx.MockTransport(up.handler))
    router.update_provider("openrouter", {"api_key": "sk-or-v1-abcdefgh1234"})
    await router.catalog("openrouter")
    ctrl.settings.update({"routes": {"vellur-image": ["openrouter:img/model"]},
                          "image_prices": {"openrouter:img/model": 0.04}})
    agent = AgentManager(ctrl, router)
    app = build_admin(ctrl, router=router, agent=agent)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://ai.example")
    now = 1_800_000_000.0
    tid = ctrl.db.x("INSERT INTO tasks(created,updated,state,model,prompt) VALUES(?,?,?,?,?)",
                    (now, now, "running", "vellur-coder", "draw a cube")).lastrowid
    body = {"prompt": "a red cube", "task_id": tid}
    r = await client.post("/autopilot/api/agent/images", json=body)
    assert r.status_code == 401
    r = await client.post("/autopilot/api/agent/images", headers={"X-Runner-Secret": "r" * 32}, json={**body, "task_id": 9999})
    assert r.status_code == 404
    r = await client.post("/autopilot/api/agent/images", headers={"X-Runner-Secret": "r" * 32}, json=body)
    assert r.status_code == 200, r.text
    iid = r.json()["images"][0]["id"]
    assert ctrl.db.one("SELECT task_id FROM generated_images WHERE id=?", (iid,))["task_id"] == tid
    r = await client.get(f"/autopilot/api/agent/images/{iid}/content", headers={"X-Runner-Secret": "r" * 32})
    assert r.status_code == 200 and r.content == PNG
