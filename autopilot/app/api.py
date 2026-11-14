"""Admin API + dashboard (internal port 8080, published only on 127.0.0.1; Caddy adds basic auth and a secret header).

Authorization is checked here on every /autopilot/api call, not only in Caddy:
  * X-Vellur-Admin must equal AUTOPILOT_ADMIN_SECRET (Caddy injects it after basic auth; browsers never see it);
  * state-changing calls also need X-Vellur-Request: 1 and a same-origin Origin header (CSRF protection).
Provider credentials never leave the server.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .lifecycle import ControlError, Controller
from .settings import SettingsError

STATIC = os.path.join(os.path.dirname(__file__), "static")


class StartBody(BaseModel):
    provider: Optional[str] = None


class ConnectBody(BaseModel):
    port: int
    host: Optional[str] = None


class ProtectBody(BaseModel):
    minutes: float


class ChatBody(BaseModel):
    messages: list[dict]
    max_tokens: int = 2048


def build_admin(ctrl: Controller, gateway_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    env = ctrl.env
    gw = httpx.AsyncClient(transport=gateway_transport, timeout=httpx.Timeout(connect=10, read=960, write=60, pool=10))

    def admin(request: Request) -> None:
        secret = env.admin_header_secret
        got = request.headers.get("x-vellur-admin", "")
        if not secret or len(secret) < 24:
            raise HTTPException(503, "admin secret not configured on the server")
        if not hmac.compare_digest(got.encode(), secret.encode()):
            raise HTTPException(401, "not authorized")

    def admin_write(request: Request) -> None:
        admin(request)
        if request.headers.get("x-vellur-request") != "1":
            raise HTTPException(403, "missing request header")
        origin = request.headers.get("origin")
        if origin:
            allowed = {o for o in (env.public_origin, f"https://{request.headers.get('host', '')}",
                                   f"http://{request.headers.get('host', '')}") if o}
            if origin not in allowed:
                raise HTTPException(403, "cross-origin request refused")

    def fail(e: Exception) -> JSONResponse:
        code = getattr(e, "code", "error")
        return JSONResponse(status_code=409 if code in ("busy",) else 400,
                            content={"error": code, "message": getattr(e, "message", str(e))})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "phase": ctrl.phase()}

    @app.get("/autopilot/", dependencies=[Depends(admin)])
    async def dashboard():
        return FileResponse(os.path.join(STATIC, "dashboard.html"), headers={"Cache-Control": "no-store"})

    @app.get("/autopilot/api/status", dependencies=[Depends(admin)])
    async def status():
        return ctrl.status()

    @app.get("/autopilot/api/events", dependencies=[Depends(admin)])
    async def events(request: Request):
        async def gen():
            v = -1
            while not await request.is_disconnected():
                v = await ctrl.wait_change(v, 5.0)
                yield f"data: {json.dumps(ctrl.status(), default=str)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/autopilot/api/history", dependencies=[Depends(admin)])
    async def history(limit: int = 100):
        sessions = ctrl.db.all("SELECT id,provider,profile,trigger,state,reason,gpu_name,dph,created_at,ready_at,"
                               "ended_at,termination_verified,dry_run FROM sessions ORDER BY id DESC LIMIT 30")
        return {"events": ctrl.db.events(max(1, min(limit, 500))), "sessions": sessions}

    @app.put("/autopilot/api/settings", dependencies=[Depends(admin_write)])
    async def put_settings(changes: dict):
        try:
            s = ctrl.settings.update(changes)
        except SettingsError as e:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": str(e)})
        ctrl.db.event("info", "settings", "settings changed", changes=changes)
        await ctrl._bump()
        return s

    @app.post("/autopilot/api/start", dependencies=[Depends(admin_write)])
    async def start(body: StartBody):
        try:
            return {"session": await ctrl.start("manual", body.provider)}
        except ControlError as e:
            return fail(e)

    @app.post("/autopilot/api/stop", dependencies=[Depends(admin_write)])
    async def stop():
        return {"session": await ctrl.stop("manual")}

    @app.post("/autopilot/api/hyperai/connect", dependencies=[Depends(admin_write)])
    async def hyperai_connect(body: ConnectBody):
        changes = {"hyperai_ssh_port": body.port}
        if body.host:
            changes["hyperai_ssh_host"] = body.host
        try:
            ctrl.settings.update(changes)
        except SettingsError as e:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": str(e)})
        try:
            return {"session": await ctrl.start("manual", "hyperai")}
        except ControlError as e:
            return fail(e)

    @app.post("/autopilot/api/protect", dependencies=[Depends(admin_write)])
    async def protect(body: ProtectBody):
        if not 1 <= body.minutes <= 240:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": "1–240 minutes"})
        return {"protected_until": ctrl.protect(body.minutes)}

    @app.post("/autopilot/api/billing/sync", dependencies=[Depends(admin_write)])
    async def billing_sync():
        return await ctrl.sync_billing()

    @app.post("/autopilot/api/providers/{name}/check", dependencies=[Depends(admin_write)])
    async def provider_check(name: str):
        """Non-billable checks only: credentials and an offer search (no instance is created)."""
        try:
            p = ctrl.provider(name)
        except ControlError as e:
            return fail(e)
        out = p.check_summary()
        if out["configured"] and p.automated:
            from .config import PROFILES
            try:
                o = await asyncio.wait_for(p.find_offer(PROFILES[ctrl.settings.get()["profile"]], ctrl.settings.get()), 60)
                out["offer"] = {"gpu": o.gpu_name, "vram_gb": o.vram_gb, "usd_per_hour": o.dph,
                                "download_cost_estimate": o.extra_cost, "location": o.location}
            except Exception as e:
                out["offer_error"] = getattr(e, "message", str(e))
        ctrl.db.event("info", "check", f"provider check {name}", result=out)
        return out

    @app.post("/autopilot/api/chat", dependencies=[Depends(admin_write)])
    async def chat(body: ChatBody):
        """Dashboard chat: goes through LiteLLM → inference proxy (so it starts the GPU like any client)."""
        payload = {"model": "vellur-coder", "messages": body.messages[-40:], "stream": True,
                   "max_tokens": max(64, min(body.max_tokens, 8192))}

        async def gen():
            try:
                async with gw.stream("POST", f"{env.gateway_url}/v1/chat/completions", json=payload,
                                     headers={"Authorization": f"Bearer {env.gateway_master_key}"}) as r:
                    if r.status_code != 200:
                        text = (await r.aread()).decode(errors="replace")
                        try:
                            msg = json.loads(text)["error"]["message"]
                        except Exception:
                            msg = text[:400]
                        yield f"data: {json.dumps({'vellur_error': msg})}\n\n"
                        return
                    async for chunk in r.aiter_raw():
                        yield chunk
            except httpx.HTTPError as e:
                yield f"data: {json.dumps({'vellur_error': f'gateway unreachable ({e.__class__.__name__})'})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    return app
