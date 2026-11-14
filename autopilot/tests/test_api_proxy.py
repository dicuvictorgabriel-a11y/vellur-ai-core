import asyncio

import httpx
import pytest

from app.api import build_admin
from app.proxy import build_proxy

SECRET = "s" * 32
ADMIN = {"X-Vellur-Admin": SECRET}
WRITE = {**ADMIN, "X-Vellur-Request": "1", "Origin": "https://ai.example"}
KEY = {"Authorization": "Bearer vllm-test"}


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ap")


async def test_admin_requires_secret_header(rig):
    ctrl = rig()
    async with client(build_admin(ctrl)) as c:
        assert (await c.get("/autopilot/api/status")).status_code == 401
        assert (await c.get("/autopilot/api/status", headers={"X-Vellur-Admin": "wrong" * 8})).status_code == 401
        assert (await c.get("/autopilot/", headers={})).status_code == 200      # the page is public (view only)
        r = await c.get("/autopilot/api/status", headers=ADMIN)
        assert r.status_code == 200 and r.json()["phase"] == "offline"
        assert (await c.get("/healthz")).status_code == 200      # unauthenticated liveness only


async def test_admin_refuses_when_secret_unset(rig):
    ctrl = rig(admin_header_secret="")
    async with client(build_admin(ctrl)) as c:
        assert (await c.get("/autopilot/api/status", headers={"X-Vellur-Admin": ""})).status_code == 401


async def test_writes_need_csrf_header_and_same_origin(rig):
    ctrl = rig()
    async with client(build_admin(ctrl)) as c:
        assert (await c.post("/autopilot/api/stop", headers=ADMIN)).status_code == 403
        bad = {**WRITE, "Origin": "https://evil.example"}
        assert (await c.post("/autopilot/api/stop", headers=bad)).status_code == 403
        assert (await c.post("/autopilot/api/stop", headers=WRITE)).status_code == 200


async def test_settings_validation_and_ceiling(rig):
    ctrl = rig()
    async with client(build_admin(ctrl)) as c:
        r = await c.put("/autopilot/api/settings", headers=WRITE, json={"monthly_budget_usd": 1000})
        assert r.status_code == 400 and "ceiling" in r.json()["message"]
        r = await c.put("/autopilot/api/settings", headers=WRITE, json={"idle_timeout_min": 25, "auto_start_paid": True})
        assert r.status_code == 200 and r.json()["idle_timeout_min"] == 25


async def test_start_stop_via_api_and_status_stream(rig):
    ctrl = rig()
    async with client(build_admin(ctrl)) as c:
        r = await c.post("/autopilot/api/start", headers=WRITE, json={})
        assert r.status_code == 200
        for _ in range(300):
            if ctrl.current()["state"] == "ready":
                break
            await asyncio.sleep(0.01)
        st = (await c.get("/autopilot/api/status", headers=ADMIN)).json()
        assert st["phase"] == "ready" and st["termination"] == "pending" and st["budget"]["current_rate_usd_per_hour"] == 0.3
        assert (await c.post("/autopilot/api/stop", headers=WRITE)).status_code == 200
        h = (await c.get("/autopilot/api/history", headers=ADMIN)).json()
        assert h["sessions"][0]["provider"] == "mock" and len(h["events"]) > 3


async def test_hyperai_connect_sets_port_and_attaches(rig):
    ctrl = rig(manual=True)
    async with client(build_admin(ctrl)) as c:
        r = await c.post("/autopilot/api/hyperai/connect", headers=WRITE, json={"port": 32387, "host": "ssh.hyper.ai"})
        assert r.status_code == 200
        assert ctrl.settings.get()["hyperai_ssh_port"] == 32387
        r = await c.post("/autopilot/api/hyperai/connect", headers=WRITE, json={"port": 99999})
        assert r.status_code == 400


async def test_proxy_forwards_and_counts_activity(rig):
    ctrl = rig()
    ctrl.settings.update({"auto_start_paid": True})
    seen = {}

    def backend(req):
        seen["auth"] = req.headers.get("authorization")
        seen["inflight"] = ctrl.inflight
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
    app = build_proxy(ctrl, transport=httpx.MockTransport(backend))
    async with client(app) as c:
        r = await c.post("/v1/chat/completions", json={"model": "vellur-coder", "messages": []},
                         headers={"Authorization": "Bearer vllm-test"})
        assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "hi"
    assert seen == {"auth": "Bearer vllm-test", "inflight": 1}
    assert ctrl.inflight == 0 and ctrl.current()["state"] == "ready"


async def test_proxy_explains_why_no_gpu(rig):
    ctrl = rig()
    app = build_proxy(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    async with client(app) as c:
        assert (await c.post("/v1/chat/completions", json={})).status_code == 401          # internal key required
        assert (await c.post("/v1/chat/completions", json={}, headers={"Authorization": "Bearer nope"})).status_code == 401
        r = await c.post("/v1/chat/completions", json={}, headers=KEY)
        assert r.status_code == 503
        err = r.json()["error"]
        assert err["code"] == "auto_start_disabled" and err["message"].startswith("VELLUR AI (auto_start_disabled)")
        ctrl.budget.record_provider_charge("mock", "all", 60, ctrl.fake_clock.t)
        r = await c.post("/v1/chat/completions", json={}, headers=KEY)
        assert r.json()["error"]["code"] == "budget"


async def test_proxy_backend_error_releases_activity(rig):
    ctrl = rig()
    ctrl.settings.update({"auto_start_paid": True})

    def down(req):
        raise httpx.ConnectError("refused")
    app = build_proxy(ctrl, transport=httpx.MockTransport(down))
    async with client(app) as c:
        r = await c.post("/v1/chat/completions", json={}, headers=KEY)
        assert r.status_code == 503 and r.json()["error"]["code"] == "provider"
    assert ctrl.inflight == 0


async def test_forget_host_key(rig):
    ctrl = rig()
    ctrl.db.kv_set("host_keys", {"ssh.hyper.ai": ["SHA256:old"]})
    async with client(build_admin(ctrl)) as c:
        r = await c.post("/autopilot/api/hostkeys/forget", headers=WRITE, json={"port": 0, "host": "ssh.hyper.ai"})
        assert r.json() == {"removed": True}
    assert ctrl.db.kv_get("host_keys") == {}


async def test_public_view_is_readonly_and_redacted(rig):
    ctrl = rig(manual=True)
    ctrl.settings.update({"hyperai_ssh_port": 32387})
    await ctrl.start("manual")
    for _ in range(300):
        if ctrl.current()["state"] == "ready":
            break
        await asyncio.sleep(0.01)
    ctrl.db.x("UPDATE sessions SET reason='manual: HyperAI workspace not reachable at ssh.example:2222' WHERE id=?",
              (ctrl.current()["id"],))
    async with client(build_admin(ctrl)) as c:
        assert (await c.get("/autopilot/")).status_code == 200                 # page: public
        r = await c.get("/autopilot/api/public/status")
        assert r.status_code == 200
        body = r.text
        d = r.json()
        assert d["public"] and d["phase"] == "ready" and "budget_usd" in d["budget"]
        for secret in ("ssh.example", "2222", "32387", "hyperai_ssh", "instance_id", "settings", "balances", "sk-"):
            assert secret not in body, secret
        # controls and details still need the admin header (Caddy adds it only after login)
        assert (await c.get("/autopilot/api/status")).status_code == 401
        assert (await c.get("/autopilot/api/history")).status_code == 401
        assert (await c.post("/autopilot/api/stop", headers={"X-Vellur-Request": "1"})).status_code == 401
        assert (await c.put("/autopilot/api/settings", json={"enabled": False})).status_code == 401
        assert (await c.get("/autopilot/api/conversations")).status_code == 401
        assert (await c.get("/autopilot/api/llm")).status_code == 401
        assert (await c.get("/autopilot/api/costs")).status_code == 401
        assert (await c.get("/autopilot/api/agent")).status_code == 401
        assert (await c.post("/autopilot/api/agent/tasks", json={"prompt": "do something"})).status_code == 401
        assert (await c.put("/autopilot/api/llm/providers/openrouter", json={"api_key": "x"})).status_code == 401


def https_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://ai.example")


FORM = {"X-Vellur-Request": "1", "Origin": "https://ai.example"}


async def test_login_flow_with_forced_password_change(rig):
    ctrl = rig()
    async with https_client(build_admin(ctrl)) as c:
        # only the server (secret header) can set a temporary password
        assert (await c.post("/autopilot/api/auth/reset", json={"password": "Temp-Pass-1234"})).status_code == 401
        r = await c.post("/autopilot/api/auth/reset", headers=ADMIN, json={"password": "Temp-Pass-1234"})
        assert r.json() == {"temporary_password": "Temp-Pass-1234", "must_change": True}
        assert (await c.get("/autopilot/api/auth/me")).json()["logged_in"] is False
        assert (await c.post("/autopilot/api/auth/login", headers=FORM, json={"password": "nope"})).status_code == 401
        assert (await c.post("/autopilot/api/auth/login", json={"password": "Temp-Pass-1234"})).status_code == 403  # CSRF
        r = await c.post("/autopilot/api/auth/login", headers=FORM, json={"password": "Temp-Pass-1234"})
        assert r.status_code == 200 and r.json()["must_change"] is True
        sc = r.headers["set-cookie"].lower()
        assert "httponly" in sc and "secure" in sc and "samesite=strict" in sc and "path=/autopilot/" in sc
        assert (await c.get("/autopilot/api/auth/me")).json() == {"logged_in": True, "must_change": True, "password_set": True}
        assert (await c.get("/autopilot/api/status")).status_code == 403          # must change first
        bad = await c.post("/autopilot/api/auth/change", headers=FORM, json={"current": "Temp-Pass-1234", "new": "short"})
        assert bad.json()["error"] == "weak_password"
        same = await c.post("/autopilot/api/auth/change", headers=FORM, json={"current": "Temp-Pass-1234", "new": "Temp-Pass-1234"})
        assert same.json()["error"] == "same_password"
        ok = await c.post("/autopilot/api/auth/change", headers=FORM, json={"current": "Temp-Pass-1234", "new": "my own long passphrase"})
        assert ok.status_code == 200
        assert (await c.get("/autopilot/api/status")).status_code == 200
        assert (await c.post("/autopilot/api/stop", headers=FORM)).status_code == 200
        assert (await c.post("/autopilot/api/stop", headers={"Origin": "https://evil.example", "X-Vellur-Request": "1"})).status_code == 403
        await c.post("/autopilot/api/auth/logout", headers=FORM)
        assert (await c.get("/autopilot/api/status")).status_code == 401
        # old password no longer works, new one does
        assert (await c.post("/autopilot/api/auth/login", headers=FORM, json={"password": "Temp-Pass-1234"})).status_code == 401
        assert (await c.post("/autopilot/api/auth/login", headers=FORM, json={"password": "my own long passphrase"})).status_code == 200
        # a server reset revokes every session
        await c.post("/autopilot/api/auth/reset", headers=ADMIN, json={})
        assert (await c.get("/autopilot/api/status")).status_code == 401


async def test_login_is_throttled(rig):
    ctrl = rig()
    async with https_client(build_admin(ctrl)) as c:
        await c.post("/autopilot/api/auth/reset", headers=ADMIN, json={"password": "Temp-Pass-1234"})
        for _ in range(5):
            assert (await c.post("/autopilot/api/auth/login", headers=FORM, json={"password": "x"})).status_code == 401
        r = await c.post("/autopilot/api/auth/login", headers=FORM, json={"password": "Temp-Pass-1234"})
        assert r.status_code == 429


async def test_generated_temporary_password(rig):
    ctrl = rig()
    async with https_client(build_admin(ctrl)) as c:
        pw = (await c.post("/autopilot/api/auth/reset", headers=ADMIN, json={})).json()["temporary_password"]
        assert len(pw) == 23 and pw.count("-") == 3
        assert "hash" not in ctrl.db.kv_get("admin_password") or pw not in str(ctrl.db.kv_get("admin_password"))
