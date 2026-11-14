import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Env  # noqa: E402
from app.db import DB  # noqa: E402
from app.lifecycle import Controller  # noqa: E402
from app.notify import Notifier  # noqa: E402
from app.providers.base import GPUProvider, InstanceStatus, ProviderError  # noqa: E402
from app.providers.mock import MockProvider  # noqa: E402
from app.settings import Settings  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Any httpx request without an explicit mock transport fails the test."""
    real_init = httpx.AsyncClient.__init__

    def guarded(self, *a, **k):
        if k.get("transport") is None:
            k["transport"] = httpx.MockTransport(lambda req: (_ for _ in ()).throw(
                AssertionError(f"real network call attempted: {req.method} {req.url}")))
        real_init(self, *a, **k)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", guarded)


class FakeTunnel:
    def __init__(self):
        self.opened, self.closed, self.fail = [], 0, None
        self.url = "http://mock-backend"

    async def open(self, host, port, **k):
        if self.fail:
            raise ProviderError("provider", self.fail)
        self.opened.append((host, port))

    async def close(self):
        self.closed += 1

    def is_open(self):
        return bool(self.opened)


class Backend:
    """Fake vLLM: /v1/models health + chat."""

    def __init__(self):
        self.up = True
        self.calls = []

    def handler(self, req: httpx.Request):
        self.calls.append(str(req.url))
        if not self.up:
            raise httpx.ConnectError("down")
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "vellur-coder"}]})
        if req.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "[protocol stub: no model]"}}]})
        return httpx.Response(404)


class ManualProvider(GPUProvider):
    name, display, automated, paid = "hyperai", "Manual test", False, True

    def __init__(self):
        self.stop_calls = 0

    def configured(self):
        return True, "ok"

    def endpoint(self):
        return "ssh.example", 2222

    async def status(self, iid):
        return InstanceStatus(iid, "running")

    async def stop(self, iid, settings):
        self.stop_calls += 1
        raise ProviderError("manual", "stop it in the console")


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_env(tmp_path, live=True, **kw):
    base = dict(live=live, hard_max_monthly_usd=50.0, hard_max_hourly_usd=0.5, admin_header_secret="s" * 32,
                data_dir=str(tmp_path), ssh_key_path=str(tmp_path / "key"), ssh_known_hosts_seed=str(tmp_path / "kh"),
                vllm_api_key="vllm-test", gateway_url="http://gateway", gateway_master_key="sk-master",
                vast_api_key="", vast_api_url="https://console.vast.ai", notify_url="", public_origin="https://ai.example",
                tick_seconds=0.01, billing_sync_seconds=10**9, request_hold_seconds=3.0,
                provision_timeout_seconds=5.0, model_load_timeout_seconds=3.0, poll_seconds=0.01)
    base.update(kw)
    (tmp_path / "key").write_text("dummy")
    (tmp_path / "key.pub").write_text("ssh-ed25519 AAAATEST test")
    return Env(**base)


@pytest.fixture
def rig(tmp_path):
    def build(live=True, provider=None, manual=False, db_path=None, **envkw):
        env = make_env(tmp_path, live=live, **envkw)
        db = DB(db_path or str(tmp_path / "autopilot.db"))
        settings = Settings(db, env)
        backend = Backend()
        mock = provider or MockProvider()
        providers = {"mock": mock}
        man = ManualProvider()
        providers["hyperai"] = man
        settings.update({"preferred_provider": "hyperai" if manual else "mock"})
        tunnel = FakeTunnel()
        http = httpx.AsyncClient(transport=httpx.MockTransport(backend.handler))
        clock = Clock()
        ctrl = Controller(db, env, settings, providers, tunnel, Notifier(db, ""), http=http, clock=clock)
        ctrl.backend, ctrl.mock, ctrl.manual, ctrl.fake_clock, ctrl.fake_tunnel = backend, mock, man, clock, tunnel
        return ctrl
    return build
