"""Workspace API (all behind the existing dashboard login): conversations, model providers and routes, costs,
agent tasks. Registered from api.py with the same `admin` / `admin_write` guards as every other private endpoint."""
from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .agent import AgentError, AgentManager
from .budget import month_key
from .config import ROLES
from .lifecycle import Controller
from .router import Router, RouterError
from .settings import SettingsError

HISTORY_CHARS = 240_000
MAX_MESSAGE = 400_000


class ConvBody(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


class MsgBody(BaseModel):
    content: str
    model: Optional[str] = None


class TargetBody(BaseModel):
    target: str


class TaskBody(BaseModel):
    prompt: str
    model: str = "vellur-coder"
    repo: str = ""
    base: str = ""


class TokenBody(BaseModel):
    token: str = ""


def register(app: FastAPI, ctrl: Controller, router: Router, agent: AgentManager, admin, admin_write) -> None:
    db = ctrl.db

    def err(e) -> JSONResponse:
        return JSONResponse(status_code=getattr(e, "status", 400),
                            content={"error": getattr(e, "code", "invalid"), "message": getattr(e, "message", str(e))})

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
        db.x("DELETE FROM messages WHERE conv_id=?", (cid,))
        db.x("DELETE FROM conversations WHERE id=?", (cid,))
        return {"ok": True}

    @app.post("/autopilot/api/conversations/{cid}/messages", dependencies=[Depends(admin_write)])
    async def post_message(cid: int, body: MsgBody):
        conv = db.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conv:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown conversation"})
        content = (body.content or "").strip()
        if not content or len(content) > MAX_MESSAGE:
            return JSONResponse(status_code=400, content={"error": "invalid", "message": "empty or too long message"})
        model = body.model if body.model in ROLES else conv["model"]
        now = time.time()
        first = db.one("SELECT COUNT(*) AS n FROM messages WHERE conv_id=?", (cid,))["n"] == 0
        db.x("INSERT INTO messages(conv_id,role,content,created) VALUES(?,?,?,?)", (cid, "user", content, now))
        title = " ".join(content.split())[:60] if first and conv["title"] == "New conversation" else conv["title"]
        db.x("UPDATE conversations SET updated=?, model=?, title=? WHERE id=?", (now, model, title, cid))
        history, used = [], 0
        for m in reversed(db.all("SELECT role, content FROM messages WHERE conv_id=? ORDER BY id", (cid,))):
            used += len(m["content"])
            if history and used > HISTORY_CHARS:
                break
            history.append({"role": m["role"], "content": m["content"]})
        history.reverse()
        memory = ctrl.settings.get()["memory"].strip()
        messages = ([{"role": "system", "content": "Notes from the owner (always apply):\n" + memory}] if memory else []) + history
        resp = await router.handle("POST", "chat/completions", "", {},
                                   json.dumps({"model": model, "messages": messages, "stream": True}).encode(), f"chat:{cid}")

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
                if text:
                    db.x("INSERT INTO messages(conv_id,role,content,created,meta) VALUES(?,?,?,?,?)",
                         (cid, "assistant", text, time.time(), json.dumps({"target": resp.headers.get("X-Vellur-Target", ""), "model": model})))
                    db.x("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))
            yield f"data: {json.dumps({'vellur_done': True, 'target': resp.headers.get('X-Vellur-Target', ''), 'title': title})}\n\n".encode()

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # ── model providers and routes ──────────────────────────────────────────────────────────────
    def llm_state() -> dict:
        st = ctrl.settings.get()
        effective = {}
        for role in ROLES:
            rows = []
            for i, t in enumerate(router.chain(role)):
                ok, code, why = router.usable(t, fallback=i > 0)
                pr = router.price(t)
                rows.append({"target": t, "usable": ok, "code": code, "why": why, "price": pr})
            effective[role] = rows
        return {"roles": ROLES, "routes": st["routes"], "prices": st["prices"], "providers": router.providers_public(),
                "effective": effective, "paid_fallback": st["paid_fallback"]}

    @app.get("/autopilot/api/llm", dependencies=[Depends(admin)])
    async def llm():
        return llm_state()

    @app.put("/autopilot/api/llm/providers/{name}", dependencies=[Depends(admin_write)])
    async def put_provider(name: str, changes: dict):
        try:
            router.update_provider(name, changes)
        except RouterError as e:
            return err(e)
        await ctrl._bump()
        return llm_state()

    @app.delete("/autopilot/api/llm/providers/{name}", dependencies=[Depends(admin_write)])
    async def delete_provider(name: str):
        router.delete_provider(name)
        return llm_state()

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
        return llm_state()

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
        return {"available": ok, "why": why, "github_token_set": bool(agent.github_token()), "tasks": agent.list(),
                "max_minutes": st["agent_max_minutes"], "max_usd": st["agent_max_usd"]}

    @app.post("/autopilot/api/agent/tasks", dependencies=[Depends(admin_write)])
    async def create_task(body: TaskBody):
        try:
            return await agent.create(body.prompt, body.model, body.repo, body.base)
        except AgentError as e:
            return err(e)

    @app.get("/autopilot/api/agent/tasks/{tid}", dependencies=[Depends(admin)])
    async def task_detail(tid: int):
        try:
            return agent.detail(tid)
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

    @app.put("/autopilot/api/agent/github", dependencies=[Depends(admin_write)])
    async def github_token(body: TokenBody):
        try:
            agent.set_github_token(body.token)
        except AgentError as e:
            return err(e)
        return {"github_token_set": bool(agent.github_token())}
