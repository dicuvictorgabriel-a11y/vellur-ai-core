"""API regression tests for the endpoints the React Mission Control drives.

Every new React view (Routing, Models, Costs fixed editor, HyperAI connect, Vast
offers) is wired to an existing JSON endpoint; these tests pin the endpoint
behaviour so a React change cannot silently break it (and vice versa).
"""

from __future__ import annotations

import pytest

from app.api import build_admin
from app.db import DB
from app.lifecycle import Controller
from app.notify import Notifier
from app.router import Router
from app.settings import Settings


class FakeTunnel:
    def up(self, *args, **kwargs):  # pragma: no cover - unused here
        return None


@pytest.fixture
def setup(tmp_path):
    import httpx

    from conftest import make_env

    from app.settings import Settings as S
    assert S is Settings

    env = make_env(tmp_path, live=False)
    db = DB(str(tmp_path / "autopilot.db"))
    settings = Settings(db, env)
    from app.providers.mock import MockProvider
    from app.providers.hyperai import HyperAIProvider

    ctrl = Controller(db, env, settings,
                      {"mock": MockProvider(), "hyperai": HyperAIProvider(settings.get, str(tmp_path / "key"))},
                      FakeTunnel(), Notifier(db, ""),
                      http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, "{}"))))
    app = build_admin(ctrl)
    headers = {"x-vellur-admin": env.admin_header_secret}
    write_headers = {**headers, "x-vellur-request": "1"}
    from fastapi.testclient import TestClient

    client = TestClient(app)
    return {"ctrl": ctrl, "db": db, "client": client, "headers": headers, "write_headers": write_headers,
            "env": env, "settings": settings}


def test_routing_state_shape_and_mode_change(setup):
    client = setup["client"]
    headers = setup["headers"]
    state = client.get("/autopilot/api/routing", headers=headers).json()
    assert state["mode"] in {"auto", "economy", "balanced", "quality", "manual"}
    assert set(state["modes"]) == {"auto", "economy", "balanced", "quality", "manual"}
    assert "allowance_usd" in state and "task_limit_usd" in state and "active" in state

    saved = client.put("/autopilot/api/routing", headers=setup["write_headers"],
                       json={"routing_mode": "auto", "auto_allowance_usd": 1.5, "auto_task_limit_usd": 0.5})
    assert saved.status_code == 200, saved.text
    after = saved.json()
    assert after["mode"] == "auto"
    assert after["allowance_usd"] == 1.5
    assert after["task_limit_usd"] == 0.5
    assert after["active"] is True
    assert client.get("/autopilot/api/routing", headers=headers).json()["mode"] == "auto"

    bad = client.put("/autopilot/api/routing", headers=setup["write_headers"], json={"routing_mode": "turbo"})
    assert bad.status_code == 400


def test_routing_unknown_decision_404(setup):
    client = setup["client"]
    headers = setup["headers"]
    response = client.get("/autopilot/api/routing/decisions/999999", headers=headers)
    assert response.status_code == 404


def test_llm_state_and_route_order_edit(setup):
    client = setup["client"]
    headers = setup["headers"]
    state = client.get("/autopilot/api/llm", headers=headers).json()
    assert "routes" in state and "effective" in state and "providers" in state

    seeded = client.put("/autopilot/api/llm/routes", headers=setup["write_headers"],
                        json={"vellur-coder": ["openai:gpt-4o-mini"], "vellur-chat": ["openai:gpt-4o-mini"]})
    assert seeded.status_code == 200, seeded.text
    reordered = {"vellur-coder": ["mock:local-model", "openai:gpt-4o-mini"]}
    saved = client.put("/autopilot/api/llm/routes", headers=setup["write_headers"], json=reordered)
    assert saved.status_code == 200, saved.text
    assert saved.json()["routes"]["vellur-coder"] == reordered["vellur-coder"]
    after = client.get("/autopilot/api/llm", headers=headers).json()
    assert after["routes"]["vellur-coder"] == reordered["vellur-coder"]

    bad = client.put("/autopilot/api/llm/routes", headers=setup["write_headers"],
                     json={"vellur-chat": ["not a target!"]})
    assert bad.status_code == 400
    unknown = client.put("/autopilot/api/llm/routes", headers=setup["write_headers"],
                         json={"vellur-robot": ["mock:local-model"]})
    assert unknown.status_code == 400


def test_provider_add_save_remove_roundtrip(setup):
    client = setup["client"]
    headers = setup["headers"]
    added = client.put("/autopilot/api/llm/providers/acme", headers=setup["write_headers"],
                       json={"base_url": "https://acme.example/v1"})
    assert added.status_code == 200, added.text
    names = [p["name"] for p in added.json()["providers"]]
    assert "acme" in names

    missing = client.put("/autopilot/api/llm/providers/bare", headers=setup["write_headers"], json={})
    assert missing.status_code == 400

    saved = client.put("/autopilot/api/llm/providers/acme", headers=setup["write_headers"],
                       json={"api_key": "sk-acme", "base_url": "https://acme.example/v1"})
    assert saved.status_code == 200, saved.text
    acme = [p for p in saved.json()["providers"] if p["name"] == "acme"][0]
    assert acme["key_set"] and acme["base_url"] == "https://acme.example/v1"

    removed = client.delete("/autopilot/api/llm/providers/acme", headers=setup["write_headers"])
    assert removed.status_code == 200, removed.text
    assert "acme" not in [p["name"] for p in removed.json()["providers"]]


def test_models_catalog_shape(setup):
    client = setup["client"]
    headers = setup["headers"]
    body = client.get("/autopilot/api/llm/models", headers=headers).json()
    assert isinstance(body.get("models"), list)
    assert isinstance(body.get("capabilities"), dict)


def test_settings_prices_and_fixed_costs_roundtrip(setup):
    client = setup["client"]
    headers = setup["headers"]
    saved = client.put("/autopilot/api/settings", headers=setup["write_headers"], json={
        "prices": {"acme:m3": [1.5, 6.0]},
        "image_max_usd": 0.35,
        "agent_image_max_usd": 0.55,
        "image_confirm_paid": True,
        "fixed_costs": [
            {"name": "Domain", "usd": 12, "in_budget": False},
            {"name": "Rent", "usd": 0.25, "in_budget": True},
        ],
    })
    assert saved.status_code == 200, saved.text
    llm = client.get("/autopilot/api/llm", headers=headers).json()
    assert llm["prices"] == {"acme:m3": [1.5, 6.0]}
    assert llm["images"]["max_usd"] == 0.35
    assert llm["images"]["agent_max_usd"] == 0.55
    assert llm["images"]["confirm_paid"] is True
    costs = client.get("/autopilot/api/costs", headers=headers).json()
    fixed = {row["name"]: row for row in costs.get("fixed_costs", [])}
    assert fixed["Domain"]["usd"] == 12.0 and fixed["Domain"]["in_budget"] is False
    assert fixed["Rent"]["usd"] == 0.25 and fixed["Rent"]["in_budget"] is True

    bad = client.put("/autopilot/api/settings", headers=setup["write_headers"], json={"prices": {"bad target": [1, 2]}})
    assert bad.status_code == 400
    bad_cost = client.put("/autopilot/api/settings", headers=setup["write_headers"], json={"fixed_costs": [{"name": "", "usd": 5}]})
    assert bad_cost.status_code == 400


def test_image_status_shape(setup):
    client = setup["client"]
    headers = setup["headers"]
    body = client.get("/autopilot/api/images/status", headers=headers).json()
    assert isinstance(body.get("available"), bool)
    assert isinstance(body.get("suggested"), list) and body["suggested"]
    assert all("target" in row for row in body["suggested"])


def test_hyperai_connect_updates_settings(setup):
    client = setup["client"]
    headers = setup["headers"]
    saved = client.post("/autopilot/api/hyperai/connect", headers=setup["write_headers"], json={"host": "ssh.hyper.ai", "port": 22220})
    assert saved.status_code == 200, saved.text
    status = client.get("/autopilot/api/status", headers=headers).json()
    assert status["settings"]["hyperai_ssh_host"] == "ssh.hyper.ai"
    assert status["settings"]["hyperai_ssh_port"] == 22220


def test_provider_check_is_non_billable(setup):
    client = setup["client"]
    headers = setup["headers"]
    body = client.post("/autopilot/api/providers/mock/check", headers=setup["write_headers"], json={}).json()
    assert body.get("configured") is True
    assert body.get("automated") is True
    assert body.get("detail") == "ok"
    assert body.get("offer", {}).get("gpu") == "MOCK GPU"
    assert "instance" not in body


def test_endpoints_reject_anonymous_calls(setup):
    client = setup["client"]
    for method, url, payload in [
        ("GET", "/autopilot/api/routing", None),
        ("PUT", "/autopilot/api/routing", {"routing_mode": "auto"}),
        ("GET", "/autopilot/api/llm", None),
        ("PUT", "/autopilot/api/llm/routes", {}),
        ("PUT", "/autopilot/api/settings", {"image_max_usd": 1}),
        ("GET", "/autopilot/api/images/status", None),
    ]:
        response = client.request(method, url, json=payload)
        assert response.status_code == 401, (method, url, response.status_code)
