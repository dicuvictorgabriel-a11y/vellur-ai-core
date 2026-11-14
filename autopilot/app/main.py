"""Entry point: one process, two HTTP servers (admin 8080, inference proxy 8000) and the watchdog."""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .agent import AgentManager
from .api import build_admin
from .config import Env
from .db import DB
from .lifecycle import Controller
from .notify import Notifier
from .providers.hyperai import HyperAIProvider
from .providers.mock import MockProvider
from .providers.vastai import VastProvider
from .proxy import build_proxy
from .router import Router
from .settings import Settings
from .tunnel import Tunnel


def build(env: Env | None = None) -> Controller:
    env = env or Env()
    db = DB(os.path.join(env.data_dir, "autopilot.db"))
    settings = Settings(db, env)
    providers = {
        "hyperai": HyperAIProvider(settings.get, env.ssh_key_path),
        "vastai": VastProvider(env.vast_api_key, env.vast_api_url, live=env.live),
    }
    if os.environ.get("AUTOPILOT_ENABLE_MOCK") == "1":
        providers["mock"] = MockProvider()
    tunnel = Tunnel(db, env.ssh_key_path, env.ssh_known_hosts_seed)
    return Controller(db, env, settings, providers, tunnel, Notifier(db, env.notify_url))


async def amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctrl = build()
    ctrl.db.event("info", "boot", f"controller started (live mode: {ctrl.env.live})")
    await ctrl.recover()
    ctrl.start_background()
    router = Router(ctrl)
    agent = AgentManager(ctrl, router)
    await agent.recover()
    servers = [
        uvicorn.Server(uvicorn.Config(build_admin(ctrl, router=router, agent=agent), host="0.0.0.0", port=8080,
                                      log_level="warning", proxy_headers=False, access_log=False)),
        uvicorn.Server(uvicorn.Config(build_proxy(ctrl, router=router), host="0.0.0.0", port=8000, log_level="warning",
                                      access_log=False, timeout_keep_alive=75)),
    ]
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        await agent.close()
        await ctrl.close()
        await ctrl.tunnel.close()


if __name__ == "__main__":
    asyncio.run(amain())
