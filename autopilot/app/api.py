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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from .auth import COOKIE, SESSION_SECONDS, Auth, AuthError, temp_password
# Context service initialized in build_admin
from .lifecycle import ControlError, Controller
from .mcp_adapter import register_mcp_routes
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
    from .efficiency import register as register_efficiency
    from .images import ImageService, register as register_images
    from .mission import register as register_mission
    from .music import register as register_music
    from .router import Router
    from .selfdev import register as register_selfdev
    from .studio3d import register as register_studio3d
    from .workspace import register as register_workspace
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    env = ctrl.env
    router = router or Router(ctrl)
    agent = agent or AgentManager(ctrl, router)
    app.state.router, app.state.agent = router, agent
    app.state.efficiency = router.efficiency
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

    # React-only frontend. The bundle lives in static/react/ (installed by bin/vac-autoupdate
    # from the CI-built release asset); it is the single proposed production entry point.
    react_root = os.path.realpath(os.path.join(STATIC, "react"))

    @app.get("/autopilot/")
    async def dashboard():
        # React-only entry point (branch nova/react-only). The legacy dashboard.html was retired;
        # it stays recoverable from the git tag legacy-dashboard-archive, not from any route.
        index = os.path.join(react_root, "index.html")
        if os.path.isfile(index):
            return FileResponse(index, headers={"Cache-Control": "no-store"})
        return JSONResponse(
            status_code=503,
            content={
                "error": "react_build_missing",
                "message": "NOVA's React app is not installed. Run 'bin/vac-autoupdate' (or build frontend/ locally); the legacy dashboard is not served anymore.",
            },
        )

    @app.get("/autopilot/3d/", dependencies=[Depends(admin)])
    async def studio3d():
        return FileResponse(os.path.join(STUDIO3D, "studio3d.html"), headers=STUDIO3D_HEADERS)

    # Compatibility alias kept for the transition: /autopilot/react/ serves the same single React app.
    @app.get("/autopilot/react")
    async def react_preview_redirect():
        return RedirectResponse("/autopilot/react/", status_code=308)

    @app.get("/autopilot/react/")
    async def react_preview():
        index = os.path.join(react_root, "index.html")
        if not os.path.isfile(index):
            raise HTTPException(404, "React preview is not built on this server")
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    @app.get("/autopilot/react/{asset:path}")
    async def react_preview_asset(asset: str):
        target = os.path.realpath(os.path.join(react_root, asset))
        if not target.startswith(react_root + os.sep) or not os.path.isfile(target):
            raise HTTPException(404, "not found")
        cache = "no-store" if asset.endswith(".html") else "public, max-age=31536000, immutable"
        return FileResponse(target, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})

    # The React build uses relative asset URLs (vite base "./"), so the same bundle works when the
    # app is served at /autopilot/ (assets resolve to /autopilot/assets/...) and at /autopilot/react/.
    @app.get("/autopilot/assets/{asset:path}")
    async def react_assets(asset: str):
        target = os.path.realpath(os.path.join(react_root, "assets", asset))
        if not target.startswith(react_root + os.sep) or not os.path.isfile(target):
            raise HTTPException(404, "not found")
        return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})

    # IBM Carbon assets (tokens, IBM Plex fonts, bundled official web components). Public: the login page uses them too.
    carbon_root = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "carbon"))

    @app.get("/autopilot/carbon/{asset:path}")
    async def carbon_asset(asset: str):
        target = os.path.realpath(os.path.join(carbon_root, asset))
        if not target.startswith(carbon_root + os.sep) or not os.path.isfile(target) or not target.endswith((".css", ".js", ".woff2")):
            raise HTTPException(404, "not found")
        return FileResponse(target, headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})

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
    register_mission(app, ctrl, router, agent, admin, admin_write, router.efficiency)
    register_music(app, ctrl, admin, admin_write)
    register_studio3d(app, ctrl, router, agent, admin, admin_write)
    app.state.selfdev = register_selfdev(app, ctrl, admin, admin_write)
    register_efficiency(app, ctrl, admin, admin_write)

    # Context Bridge: initialize context service and register MCP routes
    from .context import ContextService
    context_service = ContextService(ctrl.db)
    app.state.context_service = context_service
    register_mcp_routes(app, context_service, admin_write)

    # Context API for dashboard (behind login)
    from .context import ContextService

    @app.get("/autopilot/api/context/projects", dependencies=[Depends(admin)])
    async def list_context_projects():
        """List projects that have context data."""
        rows = ctrl.db.all("SELECT DISTINCT project FROM context_items ORDER BY project")
        return {"projects": [r["project"] for r in rows]}

    @app.get("/autopilot/api/context/{project}", dependencies=[Depends(admin)])
    async def get_project_context_api(project: str, task_id: str | None = None, max_items: int = 50, max_tokens: int = 8000):
        """Get bounded context for a project (dashboard API)."""
        return context_service.get_agent_context(project, task_id, max_items, max_tokens)

    @app.post("/autopilot/api/context/{project}/refresh", dependencies=[Depends(admin_write)])
    async def refresh_context_api(project: str, source: str | None = None):
        """Manually trigger context refresh."""
        if source:
            result = context_service.refresh_source(project, source, triggered_by="manual")
            return result.__dict__
        results = context_service.refresh_all(project, triggered_by="manual")
        return [r.__dict__ for r in results]

    @app.get("/autopilot/api/context/{project}/search", dependencies=[Depends(admin)])
    async def search_context_api(project: str, q: str, limit: int = 50):
        """Full-text search across context."""
        results = context_service.search(project, q, limit)
        return {"results": [r.to_dict() for r in results]}

    @app.get("/autopilot/api/context/{project}/refresh-log", dependencies=[Depends(admin)])
    async def refresh_log_api(project: str, source: str | None = None, limit: int = 50):
        """Get context refresh audit log."""
        sql = "SELECT * FROM context_refresh_log WHERE project=?"
        params = [project]
        if source:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = ctrl.db.all(sql, tuple(params))
        return {"refresh_log": [dict(r) for r in rows]}

    # On controller start, recover context
    @app.on_event("startup")
    async def context_startup():
        settings = ctrl.settings.get()
        project = settings.get("project", "default")
        context_service.on_controller_start(project)

    return app
