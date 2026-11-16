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

from .auth import COOKIE, SESSION_SECONDS, Auth, AuthError, temp_password
from .lifecycle import ControlError, Controller
from .settings import SettingsError

STATIC = os.path.join(os.path.dirname(__file__), "static")
STUDIO3D = os.path.join(STATIC, "3d")
# The 3D Studio is first-party UI (unlike agent-written preview pages): it may call the dashboard API with the owner's
# session. It loads only local files, and model data is never executed as code.
STUDIO3D_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; connect-src 'self' blob:; worker-src 'self' blob:; "
                               "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
    "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
}


class StartBody(BaseModel):
    provider: Optional[str] = None


class ConnectBody(BaseModel):
    port: int
    host: Optional[str] = None


class ProtectBody(BaseModel):
    minutes: float


class LoginBody(BaseModel):
    password: str


class ChangeBody(BaseModel):
    current: str
    new: str


class ResetBody(BaseModel):
    password: Optional[str] = None


class ChatBody(BaseModel):
    messages: list[dict]
    max_tokens: int = 2048


def build_admin(ctrl: Controller, gateway_transport: httpx.AsyncBaseTransport | None = None,
                router=None, agent=None) -> FastAPI:
    from .agent import AgentManager
    from .attachments import register as register_attachments
    from .images import ImageService, register as register_images
    from .mission import register as register_mission
    from .music import register as register_music
    from .router import Router
    from .workspace import register as register_workspace
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    env = ctrl.env
    router = router or Router(ctrl)
    agent = agent or AgentManager(ctrl, router)
    app.state.router, app.state.agent = router, agent
    image_service = ImageService(ctrl, router)
    app.state.images = image_service

    auth = Auth(ctrl.db)
    ALLOWED_DURING_CHANGE = {"/autopilot/api/auth/change", "/autopilot/api/auth/logout", "/autopilot/api/auth/me"}

    def via_secret(request: Request) -> bool:
        """The server-side secret header: only `vac` on the VPS (Caddy strips it from every public request)."""
        secret = env.admin_header_secret
        got = request.headers.get("x-vellur-admin", "")
        return bool(secret) and len(secret) >= 24 and hmac.compare_digest(got.encode(), secret.encode())

    def admin(request: Request) -> None:
        if via_secret(request):
            return
        if not auth.session_valid(request.cookies.get(COOKIE)):
            raise HTTPException(401, "login required")
        if auth.must_change() and request.url.path not in ALLOWED_DURING_CHANGE:
            raise HTTPException(403, "password_change_required")

    def same_origin(request: Request) -> None:
        if request.headers.get("x-vellur-request") != "1":
            raise HTTPException(403, "missing request header")
        origin = request.headers.get("origin")
        if origin:
            allowed = {o for o in (env.public_origin, f"https://{request.headers.get('host', '')}",
                                   f"http://{request.headers.get('host', '')}") if o}
            if origin not in allowed:
                raise HTTPException(403, "cross-origin request refused")

    def admin_write(request: Request) -> None:
        admin(request)
        same_origin(request)

    def client_ip(request: Request) -> str:
        xff = request.headers.get("x-forwarded-for", "")
        return xff.split(",")[-1].strip() if xff else (request.client.host if request.client else "?")

    def fail(e: Exception) -> JSONResponse:
        code = getattr(e, "code", "error")
        return JSONResponse(status_code=409 if code in ("busy",) else 400,
                            content={"error": code, "message": getattr(e, "message", str(e))})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "phase": ctrl.phase()}

    # ── public, read-only (no login): the page itself and a filtered status ─────────────────────
    public_streams = {"n": 0}
    MAX_PUBLIC_STREAMS = 20

    @app.get("/autopilot/")
    async def dashboard():
        return FileResponse(os.path.join(STATIC, "dashboard.html"), headers={"Cache-Control": "no-store"})

    @app.get("/autopilot/studio/", dependencies=[Depends(admin)])
    async def studio():
        return FileResponse(os.path.join(STATIC, "studio.html"), headers={"Cache-Control": "no-store"})

    @app.get("/autopilot/studio/{asset}", dependencies=[Depends(admin)])
    async def studio_asset(asset: str):
        if asset not in {"studio.js", "geometry.js", "studio.css"}:
            raise HTTPException(404, "not found")
        return FileResponse(os.path.join(STATIC, asset), headers={"Cache-Control": "no-store"})

    @app.get("/autopilot/3d/", dependencies=[Depends(admin)])
    async def studio3d():
        return FileResponse(os.path.join(STUDIO3D, "studio3d.html"), headers=STUDIO3D_HEADERS)

    @app.get("/autopilot/3d/{asset:path}", dependencies=[Depends(admin)])
    async def studio3d_asset(asset: str):
        root = os.path.realpath(STUDIO3D)
        target = os.path.realpath(os.path.join(root, asset))
        if not target.startswith(root + os.sep) or not os.path.isfile(target):
            raise HTTPException(404, "not found")
        return FileResponse(target, headers=STUDIO3D_HEADERS)

    @app.get("/autopilot/api/public/status")
    async def public_status():
        return ctrl.public_status()

    @app.get("/autopilot/api/public/events")
    async def public_events(request: Request):
        if public_streams["n"] >= MAX_PUBLIC_STREAMS:
            return JSONResponse(status_code=503, content={"error": "busy", "message": "too many viewers; retry"})
        public_streams["n"] += 1

        async def gen():
            try:
                v = -1
                while not await request.is_disconnected():
                    v = await ctrl.wait_change(v, 5.0)
                    yield f"data: {json.dumps(ctrl.public_status(), default=str)}\n\n"
            finally:
                public_streams["n"] -= 1
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # ── login (in-page form; no browser popup) ──────────────────────────────────────────────────
    def set_cookie(resp: JSONResponse, token: str) -> JSONResponse:
        resp.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, path="/autopilot/", secure=True, httponly=True,
                        samesite="strict")
        return resp

    @app.get("/autopilot/api/auth/me")
    async def me(request: Request):
        logged = via_secret(request) or auth.session_valid(request.cookies.get(COOKIE))
        return {"logged_in": logged, "must_change": logged and auth.must_change(), "password_set": auth.has_password()}

    @app.post("/autopilot/api/auth/login", dependencies=[Depends(same_origin)])
    async def login(body: LoginBody, request: Request):
        try:
            token = await auth.login(body.password, client_ip(request))
        except AuthError as e:
            return JSONResponse(status_code=e.status, content={"error": e.code, "message": e.message})
        return set_cookie(JSONResponse({"ok": True, "must_change": auth.must_change()}), token)

    @app.post("/autopilot/api/auth/change", dependencies=[Depends(admin_write)])
    async def change(body: ChangeBody, request: Request):
        try:
            await auth.change(body.current, body.new)
        except AuthError as e:
            return JSONResponse(status_code=e.status, content={"error": e.code, "message": e.message})
        token = await auth.login(body.new, client_ip(request))   # fresh session (all old ones were revoked)
        return set_cookie(JSONResponse({"ok": True}), token)

    @app.post("/autopilot/api/auth/logout", dependencies=[Depends(same_origin)])
    async def logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/autopilot/")
        return resp

    @app.post("/autopilot/api/auth/reset")
    async def reset(body: ResetBody, request: Request):
        """Server only (secret header, i.e. `sudo vac autopilot password`): set a temporary password."""
        if not via_secret(request):
            raise HTTPException(401, "server only")
        pw = (body.password or "").strip() or temp_password()
        if len(pw) < 8:
            return JSONResponse(status_code=400, content={"error": "weak_password", "message": "at least 8 characters"})
        await asyncio.to_thread(auth.set_password, pw, True)
        return {"temporary_password": pw, "must_change": True}

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

    @app.post("/autopilot/api/hostkeys/forget", dependencies=[Depends(admin_write)])
    async def forget_host(body: ConnectBody):
        """Forget the trusted SSH host key of a host (after a legitimate key change). Server-only via vac."""
        host = (body.host or "").strip()
        keys = ctrl.db.kv_get("host_keys", {}) or {}
        removed = keys.pop(host, None) is not None
        ctrl.db.kv_set("host_keys", keys)
        ctrl.db.event("alert" if removed else "info", "security", f"SSH host key for {host} forgotten" if removed
                      else f"no trusted SSH host key for {host}")
        return {"removed": removed}

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

    register_attachments(app, ctrl, agent.attachments, admin, admin_write)
    register_images(app, ctrl, router, image_service, admin, admin_write)
    register_workspace(app, ctrl, router, agent, admin, admin_write)
    register_mission(app, ctrl, router, agent, admin, admin_write)
    register_music(app, ctrl, admin, admin_write)

    return app
