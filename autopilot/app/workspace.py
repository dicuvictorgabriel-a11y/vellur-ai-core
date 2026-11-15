"""Workspace API (all behind the existing dashboard login): conversations, model providers and routes, costs,
agent tasks. Registered from api.py with the same `admin` / `admin_write` guards as every other private endpoint."""
from __future__ import annotations

import hmac
import json
import os
import time
from typing import Optional

import asyncio
import re

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .agent import AgentError, AgentManager
from .budget import month_key
from .images import ImageError, ImageService, wants_image
from .config import ROLES
from .lifecycle import Controller
from .model_intel import summary
from .release import Release, ReleaseError
from .router import Router, RouterError
from .settings import SettingsError

HISTORY_CHARS = 240_000
MAX_MESSAGE = 400_000


class ConvBody(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


class MsgBody(BaseModel):
    content: str = ""
    model: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)
    confirm_image: bool = False
    image_size: str = "1024x1024"
    request_id: Optional[str] = None


class TargetBody(BaseModel):
    target: str


class AgentImageBody(BaseModel):
    prompt: str
    task_id: int
    size: str = "1024x1024"
    n: int = 1


class TaskBody(BaseModel):
    prompt: str
    model: str = "vellur-coder"
    repo: str = ""
    base: str = ""
    visual_inspection: Optional[bool] = None
    auto_screenshots: Optional[bool] = None
    attachments: list[str] = Field(default_factory=list)


class FollowBody(BaseModel):
    text: str
    model: Optional[str] = None
    visual_inspection: Optional[bool] = None
    auto_screenshots: Optional[bool] = None
    attachments: list[str] = Field(default_factory=list)


class CheckpointBody(BaseModel):
    label: str = ""
    id: str = ""


class ReleaseBody(BaseModel):
    sha: str = ""


class ReleaseReport(BaseModel):
    installed: str = ""
    previous: str = ""
    last: str = ""
    clear: str = ""


class TokenBody(BaseModel):
    token: str = ""


class BlendBody(BaseModel):
    path: str = ""


# Preview pages are code written by the agent. They are served sandboxed (no access to the dashboard's origin,
# cookies or API) under a short-lived link that only a logged-in owner can create.
PREVIEW_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups allow-modals allow-pointer-lock allow-downloads",
    "Access-Control-Allow-Origin": "*", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow", "X-Content-Type-Options": "nosniff",
}


def _brief(t: dict) -> dict:
    return {k: t.get(k) for k in ("id", "state", "outcome", "runs", "files_changed", "pr_url", "cost_usd", "error", "activity")}


def register(app: FastAPI, ctrl: Controller, router: Router, agent: AgentManager, admin, admin_write) -> None:
    db = ctrl.db
    attachments = agent.attachments
    images = ImageService(ctrl, router)

    def err(e) -> JSONResponse:
        body = {"error": getattr(e, "code", "invalid"), "message": getattr(e, "message", str(e))}
        details = getattr(e, "data", None)
        if details:
            body["details"] = details
        return JSONResponse(status_code=getattr(e, "status", 400), content=body)

    def runner_only(request: Request) -> None:
        secret = ctrl.env.agent_runner_secret
        got = request.headers.get("x-runner-secret", "")
        if not secret or len(secret) < 24 or not hmac.compare_digest(got.encode(), secret.encode()):
            raise HTTPException(401, "runner authentication required")

    def visual_prefs(body) -> tuple:
        st = ctrl.settings.get()
        return (st["agent_visual_inspection"] if body.visual_inspection is None else body.visual_inspection,
                st["agent_auto_screenshots"] if body.auto_screenshots is None else body.auto_screenshots)

    # ── conversations ───────────────────────────────────────────────────────────────────────────
    @app.get("/autopilot/api/conversations", dependencies=[Depends(admin)])
    async def conversations():
        return {"conversations": db.all("SELECT id,title,model,created,updated FROM conversations ORDER BY updated DESC LIMIT 200")}

    @app.post("/autopilot/api/conversations", dependencies=[Depends(admin_write)])
    async def new_conversation(body: ConvBody):
        model = body.model if body.model in ROLES else "vellur-chat"
        now = time.time()
        cur = db.x("INSERT INTO conversations(title,model,created,updated) VALUES(?,?,?,?)",
                   ((body.title or "New conversation")[:120], model, now, now))
        return db.one("SELECT * FROM conversations WHERE id=?", (cur.lastrowid,))

    @app.get("/autopilot/api/conversations/{cid}", dependencies=[Depends(admin)])
    async def conversation(cid: int):
        conv = db.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conv:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown conversation"})
        msgs = db.all("SELECT id,role,content,created,meta FROM messages WHERE conv_id=? ORDER BY id", (cid,))
        for m in msgs:
            m["meta"] = json.loads(m["meta"] or "{}")
        return {**conv, "messages": msgs}

    @app.patch("/autopilot/api/conversations/{cid}", dependencies=[Depends(admin_write)])
    async def edit_conversation(cid: int, body: ConvBody):
        if body.title:
            db.x("UPDATE conversations SET title=? WHERE id=?", (body.title[:120], cid))
        if body.model in ROLES:
            db.x("UPDATE conversations SET model=? WHERE id=?", (body.model, cid))
        return db.one("SELECT * FROM conversations WHERE id=?", (cid,)) or {}

    @app.delete("/autopilot/api/conversations/{cid}", dependencies=[Depends(admin_write)])
    async def delete_conversation(cid: int):
        attachments.delete_for_conversation(cid)
        images.store.delete_for_conversation(cid)
        db.x("DELETE FROM messages WHERE conv_id=?", (cid,))
        db.x("DELETE FROM conversations WHERE id=?", (cid,))
        return {"ok": True}

    @app.post("/autopilot/api/conversations/{cid}/messages", dependencies=[Depends(admin_write)])
    async def post_message(cid: int, body: MsgBody):
        conv = db.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conv:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown conversation"})
        content = (body.content or "").strip()
        if len(content) > MAX_MESSAGE:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": "message is too long"})
        try:
            rows = attachments.select(body.attachments, conv_id=cid)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": str(e)})
        if not content and not rows:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": "empty message"})
        model = body.model if body.model in ROLES else conv["model"]
        now = time.time()
        first = db.one("SELECT COUNT(*) AS n FROM messages WHERE conv_id=?", (cid,))["n"] == 0
        meta = {"attachments": [attachments.public(r) for r in rows]} if rows else {}
        db.x("INSERT INTO messages(conv_id,role,content,created,meta) VALUES(?,?,?,?,?)",
             (cid, "user", content, now, json.dumps(meta)))
        if first and conv["title"] == "New conversation":
            title = " ".join(content.split())[:60] or (rows[0]["name"][:60] if rows else "New conversation")
        else:
            title = conv["title"]
        db.x("UPDATE conversations SET updated=?, model=?, title=? WHERE id=?", (now, model, title, cid))
        has_images = any(r["kind"] == "image" and r["mime"] != "image/svg+xml" for r in rows)
        make_image = model == "vellur-image" or (wants_image(content, has_images) and not rows)
        if make_image:
            async def image_gen():
                try:
                    result = await images.generate(content, "vellur-image", body.image_size, 1, body.confirm_image,
                                                   f"image:chat:{cid}", conv_id=cid,
                                                   max_usd=ctrl.settings.get().get("image_max_usd"),
                                                   request_id=body.request_id)
                except ImageError as e:
                    if e.code == "image_confirmation":
                        yield f"data: {json.dumps({'vellur_image_confirm': e.data, 'prompt': content})}\n\n".encode()
                    else:
                        yield f"data: {json.dumps({'vellur_error': e.message, 'code': e.code})}\n\n".encode()
                    return
                if result.get("duplicate"):
                    yield f"data: {json.dumps({'vellur_image': result, 'title': title})}\n\n".encode()
                    return
                text = "Generated image"
                db.x("INSERT INTO messages(conv_id,role,content,created,meta) VALUES(?,?,?,?,?)",
                     (cid, "assistant", text, time.time(), json.dumps({"images": result["images"], "model": "vellur-image",
                                                                       "target": result["target"], "cost_usd": result["cost_usd"],
                                                                       "cost_source": result["cost_source"]})))
                db.x("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))
                yield f"data: {json.dumps({'vellur_image': result, 'title': title})}\n\n".encode()
                yield f"data: {json.dumps({'vellur_done': True, 'target': result['target'], 'title': title})}\n\n".encode()
            return StreamingResponse(image_gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
        vision = bool(ctrl.settings.get().get("agent_vision") or router.can_vision(model))
        route_cap = "vision" if has_images and router.can_vision(model) else None
        history, used = [], 0
        for m in reversed(db.all("SELECT role, content, meta FROM messages WHERE conv_id=? ORDER BY id", (cid,))):
            mmeta = json.loads(m.get("meta") or "{}")
            mrows = [r for r in (attachments.get(a.get("id", "")) for a in mmeta.get("attachments", [])) if r]
            used += len(m["content"]) + sum(len(r.get("extracted") or "") + len(r["name"]) for r in mrows)
            if history and used > HISTORY_CHARS:
                break
            history.append({"role": m["role"], "content": attachments.chat_content(m["content"], mrows, vision)})
        history.reverse()
        memory = ctrl.settings.get()["memory"].strip()
        messages = ([{"role": "system", "content": "Notes from the owner (always apply):\n" + memory}] if memory else []) + history
        started = ctrl.clock()
        resp = await router.handle("POST", "chat/completions", "", {},
                                   json.dumps({"model": model, "messages": messages, "stream": True}).encode(), f"chat:{cid}",
                                   capability=route_cap)

        async def gen():
            if resp.status != 200:
                data = resp.content if resp.content is not None else b"".join([c async for c in resp.stream])
                try:
                    msg = json.loads(data)["error"]["message"]
                except Exception:  # noqa: BLE001
                    msg = data.decode(errors="replace")[:400]
                yield f"data: {json.dumps({'vellur_error': msg})}\n\n".encode()
                return
            parts, buf = [], b""
            try:
                async for chunk in resp.stream:
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        if line.startswith(b"data:") and b'"delta"' in line:
                            try:
                                d = json.loads(line[5:].strip())["choices"][0]["delta"]
                                if d.get("content"):
                                    parts.append(d["content"])
                            except (ValueError, KeyError, IndexError, TypeError):
                                pass
                    yield chunk
            finally:
                text = "".join(parts)
                spent = db.one("SELECT cost_usd,cost_source,in_tokens,out_tokens FROM usage WHERE tag=? AND ts>=? "
                               "ORDER BY id DESC LIMIT 1", (f"chat:{cid}", started)) or {}
                usage_meta = {"cost_usd": float(spent.get("cost_usd") or 0), "cost_source": spent.get("cost_source") or "",
                              "in_tokens": int(spent.get("in_tokens") or 0), "out_tokens": int(spent.get("out_tokens") or 0)}
                if text:
                    db.x("INSERT INTO messages(conv_id,role,content,created,meta) VALUES(?,?,?,?,?)",
                         (cid, "assistant", text, time.time(), json.dumps({"target": resp.headers.get("X-Vellur-Target", ""),
                                                                           "model": model, **usage_meta})))
                    db.x("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))
            yield f"data: {json.dumps({'vellur_done': True, 'target': resp.headers.get('X-Vellur-Target', ''), 'title': title, **usage_meta})}\n\n".encode()

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # ── model providers and routes ──────────────────────────────────────────────────────────────
    def llm_state_sync() -> dict:
        st = ctrl.settings.get()
        exhausted = router.budget_exhausted()          # computed once for every route and catalog entry below
        effective = {}
        for role in ROLES:
            rows = []
            capability = "image_generation" if role == "vellur-image" else None
            for i, t in enumerate(router.chain(role)):
                ok, code, why = router.usable(t, fallback=i > 0, capability=capability, budget_exhausted=exhausted)
                pr = router.price(t)
                rows.append({"target": t, "usable": ok, "code": code, "why": why, "price": pr})
            effective[role] = rows
        return {"roles": ROLES, "routes": st["routes"], "prices": st["prices"],
                "image_prices": st.get("image_prices", {}), "providers": router.providers_public(),
                "effective": effective, "paid_fallback": st["paid_fallback"],
                "capabilities": router.capability_summary(exhausted),
                "images": {"max_usd": st.get("image_max_usd"), "confirm_paid": st.get("image_confirm_paid"),
                           "agent": st.get("agent_image_generation"), "agent_max_usd": st.get("agent_image_max_usd")}}

    async def llm_state() -> dict:
        # Synchronous database and catalog work runs in a worker thread, so it can never stall the event loop
        # (health checks, streaming chats, the agent's live timeline keep answering while it runs).
        return await asyncio.to_thread(llm_state_sync)

    @app.get("/autopilot/api/llm", dependencies=[Depends(admin)])
    async def llm():
        return await llm_state()

    @app.put("/autopilot/api/llm/providers/{name}", dependencies=[Depends(admin_write)])
    async def put_provider(name: str, changes: dict):
        try:
            router.update_provider(name, changes)
        except RouterError as e:
            return err(e)
        await ctrl._bump()
        return await llm_state()

    @app.delete("/autopilot/api/llm/providers/{name}", dependencies=[Depends(admin_write)])
    async def delete_provider(name: str):
        router.delete_provider(name)
        return await llm_state()

    @app.post("/autopilot/api/llm/providers/{name}/catalog", dependencies=[Depends(admin_write)])
    async def catalog(name: str):
        try:
            models = await router.catalog(name)
        except RouterError as e:
            return err(e)
        return {"models": models}

    @app.get("/autopilot/api/llm/providers/{name}/catalog", dependencies=[Depends(admin)])
    async def cached_catalog(name: str):
        return db.kv_get(f"llm_catalog_{name}") or {"models": [], "ts": None}

    @app.post("/autopilot/api/agent/images", dependencies=[Depends(runner_only)])
    async def agent_image(body: AgentImageBody):
        st = ctrl.settings.get()
        if not st.get("agent_image_generation"):
            return JSONResponse(status_code=403, content={"error": "image_disabled",
                                                          "message": "Image generation for agent tasks is disabled in Models."})
        if not db.one("SELECT id FROM tasks WHERE id=?", (body.task_id,)):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown task"})
        try:
            # Nobody is there to confirm an agent's image: a paid model whose price is unknown cannot be checked against
            # the per-image limit before it is called, so the agent may only use it once a manual price is set.
            est = images.estimate(body.prompt, "vellur-image", body.size, body.n, st.get("agent_image_max_usd"))
            if not est["cost_known"]:
                return JSONResponse(status_code=402, content={
                    "error": "image_price_unknown",
                    "message": "The image model has no known price per image, so the agent cannot check it against its "
                               "limit. Set a manual image price in Models to let agent tasks use it."})
            return await images.generate(body.prompt, "vellur-image", body.size, body.n, True,
                                         f"task:{body.task_id}", task_id=body.task_id,
                                         max_usd=st.get("agent_image_max_usd"))
        except ImageError as e:
            return JSONResponse(status_code=e.status, content={"error": e.code, "message": e.message, **e.data})

    @app.get("/autopilot/api/agent/images/{iid}/content", dependencies=[Depends(runner_only)])
    async def agent_image_content(iid: str):
        row = images.store.get(iid)
        if not row:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown image"})
        try:
            path = images.store.path(row)
            if not os.path.isfile(path):
                raise OSError("missing")
        except (OSError, ValueError):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "image file is missing"})
        return FileResponse(path, media_type=row["mime"], headers={"Cache-Control": "private, no-store",
                                                                  "X-Content-Type-Options": "nosniff"})

    @app.get("/autopilot/api/llm/models", dependencies=[Depends(admin)])
    async def llm_models():
        def build() -> dict:
            rows = router.models()
            return {"models": rows, "capabilities": summary(rows)}
        return await asyncio.to_thread(build)

    @app.post("/autopilot/api/llm/test", dependencies=[Depends(admin_write)])
    async def test_target(body: TargetBody):
        try:
            return await router.probe(body.target)
        except RouterError as e:
            return err(e)

    @app.put("/autopilot/api/llm/routes", dependencies=[Depends(admin_write)])
    async def put_routes(routes: dict):
        try:
            ctrl.settings.update({"routes": routes})
        except SettingsError as e:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": str(e)})
        db.event("info", "llm", "model routes changed", routes=routes)
        return await llm_state()

    # ── costs ───────────────────────────────────────────────────────────────────────────────────
    @app.get("/autopilot/api/costs", dependencies=[Depends(admin)])
    async def costs():
        st = ctrl.settings.get()
        m = month_key(ctrl.clock())
        by_target = db.all(
            "SELECT target, cost_source, COUNT(*) AS requests, SUM(ok) AS ok, SUM(in_tokens) AS in_tokens, "
            "SUM(out_tokens) AS out_tokens, SUM(cached_tokens) AS cached_tokens, SUM(cost_usd) AS cost_usd, "
            "CAST(AVG(latency_ms) AS INTEGER) AS avg_ms FROM usage WHERE month=? GROUP BY target, cost_source "
            "ORDER BY cost_usd DESC, requests DESC", (m,))
        by_day = db.all("SELECT date(ts,'unixepoch') AS day, COUNT(*) AS requests, SUM(cost_usd) AS cost_usd, "
                        "SUM(in_tokens+out_tokens) AS tokens FROM usage WHERE month=? GROUP BY day ORDER BY day", (m,))
        by_use = db.all("SELECT CASE WHEN tag LIKE 'task:%' THEN 'agent tasks' WHEN tag LIKE 'chat:%' THEN 'workspace chat' "
                        "WHEN tag='probe' THEN 'tests' ELSE 'API clients (OpenCode, apps)' END AS use, COUNT(*) AS requests, "
                        "SUM(cost_usd) AS cost_usd FROM usage WHERE month=? GROUP BY use ORDER BY cost_usd DESC", (m,))
        subs = [{"name": p["display"], "usd": p["monthly_usd"]} for p in router.providers().values()
                if p["plan"] == "subscription" and p["enabled"] and p["monthly_usd"]]
        return {"month": m, "budget": ctrl.budget.status(st, ctrl.current(), ctrl.clock()), "by_target": by_target,
                "by_day": by_day, "by_use": by_use, "fixed_costs": st["fixed_costs"], "subscriptions": subs}

    # ── agent tasks ─────────────────────────────────────────────────────────────────────────────
    @app.get("/autopilot/api/agent", dependencies=[Depends(admin)])
    async def agent_state():
        ok, why = agent.available()
        st = ctrl.settings.get()
        return {"available": ok, "why": why, "github_token_set": bool(agent.github_token()),
                "github": await agent.github_status(), "tasks": agent.list(),
                "self_repo": ctrl.env.self_repo, "vision": bool(st.get("agent_vision")),
                "max_minutes": st["agent_max_minutes"], "max_usd": st["agent_max_usd"]}

    @app.post("/autopilot/api/agent/tasks", dependencies=[Depends(admin_write)])
    async def create_task(body: TaskBody):
        try:
            visual, shots = visual_prefs(body)
            return await agent.create(body.prompt, body.model, body.repo, body.base, visual, shots, body.attachments)
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/tasks/{tid}", dependencies=[Depends(admin)])
    async def task_detail(tid: int):
        try:
            return agent.detail(tid)
        except AgentError as e:
            return err(e)

    @app.post("/autopilot/api/agent/tasks/{tid}/messages", dependencies=[Depends(admin_write)])
    async def task_message(tid: int, body: FollowBody):
        try:
            visual, shots = visual_prefs(body)
            return await agent.message(tid, body.text, body.model, visual, shots, body.attachments)
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/tasks/{tid}/events", dependencies=[Depends(admin)])
    async def task_events(tid: int, after: int = 0, limit: int = 500):
        t = agent.task(tid)
        if not t:
            return JSONResponse({"error": "not_found", "message": "unknown task"}, status_code=404)
        limit = max(1, min(limit, 5000))
        evs = agent.events(tid, after, limit)
        return {"events": evs, "task": _brief(t), "has_more": len(evs) == limit}

    @app.get("/autopilot/api/agent/tasks/{tid}/stream", dependencies=[Depends(admin)])
    async def task_stream(tid: int, request: Request, after: int = 0):
        """Server-sent events: the task's timeline as it happens. Reconnects resume from Last-Event-ID."""
        if not agent.task(tid):
            return JSONResponse({"error": "not_found", "message": "unknown task"}, status_code=404)
        last = request.headers.get("last-event-id", "")
        cursor = int(last) if last.isdigit() else after

        async def gen():
            nonlocal cursor
            brief, quiet, t0, beat = None, 0, time.monotonic(), time.monotonic()
            yield "retry: 2000\n\n"
            while time.monotonic() - t0 < 600:
                if await request.is_disconnected():
                    return
                for e in agent.events(tid, cursor, 200):
                    cursor = e["id"]
                    yield f"id: {e['id']}\nevent: ev\ndata: {json.dumps(e)}\n\n"
                t = agent.task(tid)
                b = _brief(t)
                if b != brief:
                    brief = b
                    yield f"event: task\ndata: {json.dumps(b)}\n\n"
                if t["state"] in ("done", "failed", "cancelled", "timeout", "over_budget"):
                    quiet += 1
                    if quiet > 3:
                        yield "event: end\ndata: {}\n\n"
                        return
                if time.monotonic() - beat > 15:
                    beat = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.8)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/autopilot/api/agent/tasks/{tid}/diff", dependencies=[Depends(admin)])
    async def task_diff(tid: int):
        try:
            return agent.diff(tid)
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/tasks/{tid}/shots/{name}", dependencies=[Depends(admin)])
    async def task_shot(tid: int, name: str):
        p = agent.shot_path(tid, name)
        if not p:
            return JSONResponse({"error": "not_found", "message": "no such screenshot"}, status_code=404)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/autopilot/api/agent/tasks/{tid}/preview", dependencies=[Depends(admin_write)])
    async def task_preview(tid: int):
        """What can be previewed for this task, plus a 12-hour sandboxed link for the Studio pane."""
        try:
            info = await agent.preview_info(tid)
            tok = agent.preview_link(tid)
        except AgentError as e:
            return err(e)
        return {**info, "url": f"/autopilot/preview/{tok}/"}

    @app.api_route("/autopilot/preview/{token}/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def preview(token: str, path: str, request: Request):
        if request.method == "OPTIONS":            # the sandboxed page has no origin, so browsers pre-check its requests
            return Response(status_code=204, headers={**PREVIEW_HEADERS, "Access-Control-Allow-Methods": "GET, HEAD, POST, PUT, PATCH, DELETE",
                                                      "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
                                                      "Access-Control-Max-Age": "600"})
        tid = agent.preview_task(token)
        if not tid:
            return JSONResponse({"error": "expired", "message": "This preview link has expired. Open the preview again from VELLUR."},
                                status_code=404, headers=PREVIEW_HEADERS)
        body = await request.body() if request.method in ("POST", "PUT", "PATCH") else b""
        if len(body) > 20 * 1024 * 1024:
            return JSONResponse({"error": "too_large"}, status_code=413, headers=PREVIEW_HEADERS)
        fwd = {k: v for k, v in request.headers.items() if k.lower() in ("accept", "content-type", "accept-language", "range", "x-vellur-request")}
        try:
            r = await agent.preview_fetch(tid, request.method, path, request.url.query, fwd, body)
        except AgentError as e:
            return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status, headers=PREVIEW_HEADERS)
        ctype = r.headers.get("content-type", "application/octet-stream")
        content = r.content
        inner, prefix = f"/tasks/{tid}/preview/", f"/autopilot/preview/{token}/"
        if ctype.startswith("text/html"):          # the runner keeps links inside its own preview path; map it to this link
            content = content.replace(inner.encode(), prefix.encode())
        headers = dict(PREVIEW_HEADERS)
        if r.headers.get("location"):
            loc = r.headers["location"]
            headers["Location"] = prefix + loc[len(inner):] if loc.startswith(inner) else loc
        return Response(content=content, status_code=r.status_code, media_type=ctype, headers=headers)

    @app.get("/autopilot/api/agent/tasks/{tid}/models", dependencies=[Depends(admin)])
    async def task_models(tid: int):
        """3D assets in this task's workspace (GLB, glTF, OBJ, STL, .blend). Used by VELLUR 3D Studio."""
        try:
            return await agent.models(tid)
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/tasks/{tid}/asset/{path:path}", dependencies=[Depends(admin)])
    async def task_asset(tid: int, path: str):
        """Stream one 3D asset through the controller. The runner confines the path to the task workspace."""
        try:
            r = await agent.model_asset(tid, path)
        except AgentError as e:
            return err(e)
        if r.status_code >= 400:
            message = r.text[:300] if r.status_code not in (404,) else "asset not found"
            return JSONResponse(status_code=r.status_code if r.status_code in (400, 404, 413) else 502,
                                content={"error": "asset", "message": message})
        ctype = r.headers.get("content-type", "application/octet-stream")
        return Response(content=r.content, media_type=ctype,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                 "Content-Disposition": "inline"})

    @app.post("/autopilot/api/agent/tasks/{tid}/blend-export", dependencies=[Depends(admin_write)])
    async def task_blend_export(tid: int, body: BlendBody):
        """Prepare a browser preview of a .blend file with a headless Blender (optional dependency)."""
        try:
            return await agent.export_blend(tid, body.path)
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/tasks/{tid}/checkpoints", dependencies=[Depends(admin)])
    async def task_checkpoints(tid: int):
        try:
            return {"checkpoints": await agent.checkpoints(tid)}
        except AgentError as e:
            return err(e)

    @app.post("/autopilot/api/agent/tasks/{tid}/checkpoints", dependencies=[Depends(admin_write)])
    async def task_checkpoint_save(tid: int, body: CheckpointBody):
        try:
            return await agent.save_checkpoint(tid, body.label)
        except AgentError as e:
            return err(e)

    @app.post("/autopilot/api/agent/tasks/{tid}/restore", dependencies=[Depends(admin_write)])
    async def task_checkpoint_restore(tid: int, body: CheckpointBody):
        try:
            return await agent.restore_checkpoint(tid, body.id)
        except AgentError as e:
            return err(e)

    @app.post("/autopilot/api/agent/tasks/{tid}/publish-direct", dependencies=[Depends(admin_write)])
    async def publish_direct(tid: int):
        try:
            return await agent.publish_direct(tid)
        except AgentError as e:
            return err(e)

    @app.post("/autopilot/api/agent/tasks/{tid}/cancel", dependencies=[Depends(admin_write)])
    async def cancel_task(tid: int):
        try:
            return await agent.cancel(tid)
        except AgentError as e:
            return err(e)

    @app.post("/autopilot/api/agent/tasks/{tid}/publish", dependencies=[Depends(admin_write)])
    async def publish_task(tid: int):
        try:
            return await agent.publish(tid)
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/repos", dependencies=[Depends(admin)])
    async def agent_repos():
        try:
            return {"github_token_set": bool(agent.github_token()), "repos": await agent.repos()}
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/branches", dependencies=[Depends(admin)])
    async def agent_branches(repo: str = ""):
        try:
            return await agent.branches(repo)
        except AgentError as e:
            return err(e)

    # ── releases of VELLUR itself (approval only; installing is done by vac-autoupdate on the host) ──
    release = Release(ctrl.db, ctrl.env.self_repo, agent.github_token, agent.github_transport)

    @app.get("/autopilot/api/release", dependencies=[Depends(admin)])
    async def release_overview():
        return await release.overview()

    @app.post("/autopilot/api/release/approve", dependencies=[Depends(admin_write)])
    async def release_approve(body: ReleaseBody):
        try:
            return await release.approve(body.sha)
        except ReleaseError as e:
            return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)

    @app.post("/autopilot/api/release/cancel", dependencies=[Depends(admin_write)])
    async def release_cancel():
        return release.cancel()

    @app.post("/autopilot/api/release/rollback", dependencies=[Depends(admin_write)])
    async def release_rollback():
        return release.request_rollback()

    @app.get("/autopilot/api/release/pending", dependencies=[Depends(admin)])
    async def release_pending():
        return release.pending()

    @app.post("/autopilot/api/release/report", dependencies=[Depends(admin_write)])
    async def release_report(body: ReleaseReport):
        return release.report(body.installed, body.previous, body.last, body.clear)

    @app.get("/autopilot/api/agent/github/check", dependencies=[Depends(admin)])
    async def github_check(repo: str = "", branch: str = ""):
        try:
            return await agent.github_check(repo, branch)
        except AgentError as e:
            return err(e)

    @app.put("/autopilot/api/agent/github", dependencies=[Depends(admin_write)])
    async def github_token(body: TokenBody):
        try:
            agent.set_github_token(body.token)
        except AgentError as e:
            return err(e)
        return {"github_token_set": bool(agent.github_token()), "github": await agent.github_status()}
