"""Mission Control: the persistent operational brain of the workspace.

One active objective, an idea inbox, a backlog, and the next task — all linked to the
real agent execution. Nothing here invents results: the state of a linked item is always
derived from the agent task record, and costs are only real recorded costs (estimates are
labeled as estimates). Ordinary task management costs nothing; the only model call is the
explicit "suggest a plan" action.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .agent import FINAL, ROLES, AgentError, AgentManager
from .lifecycle import Controller
from .router import Router

ITEM_STATES = ("idea", "backlog", "ready", "running", "review", "verified", "parked")
# states the owner can set directly; "running" comes only from starting an agent task,
# "review" is derived when the linked task finishes, "verified" only from an explicit review.
DIRECT_STATES = ("idea", "backlog", "ready", "parked")

PLAN_SYSTEM = ("Ești asistentul NOVA. Propune un plan pentru o sarcină din Mission Control.\n"
               "- Scrie în limba română, între 3 și 10 pași, fiecare pe o linie, fără numere și fără Markdown.\n"
               "- Doar pași concreți și verificabili; nu inventa rezultate, termene, costuri sau lucruri neîntrebate.")


def err(code: str, message: str, status: Optional[int] = None) -> JSONResponse:
    if status is None:
        status = 404 if code == "not_found" else (409 if code == "busy" else 400)
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def effective_state(item: dict, task: Optional[dict]) -> str:
    """The state the owner sees: a started item mirrors its agent task until the owner judges it."""
    if item["state"] == "running":
        if task is None:
            return "backlog"          # link to a task that no longer exists: back to work, nothing invented
        return "review" if task["state"] in FINAL else "running"
    return item["state"]


def public_item(item: dict, task: Optional[dict]) -> dict:
    out = dict(item)
    out["effective_state"] = effective_state(item, task)
    out["agent_task"] = ({"id": task["id"], "state": task["state"], "outcome": task["outcome"],
                          "cost_usd": task["cost_usd"], "title": " ".join((task["prompt"] or "").split())[:80]}
                         if task else None)
    return out


class ObjectiveBody(BaseModel):
    id: Optional[int] = None
    title: str
    detail: str = ""
    cost_estimate_usd: Optional[float] = None


class ItemBody(BaseModel):
    title: str
    detail: str = ""
    state: str = "backlog"
    plan: str = ""
    cost_estimate_usd: Optional[float] = None
    depends_on: Optional[int] = None
    repo: str = ""
    base: str = ""
    objective_id: Optional[int] = None


class ItemUpdate(BaseModel):
    title: Optional[str] = None
    detail: Optional[str] = None
    state: Optional[str] = None
    plan: Optional[str] = None
    blocked_reason: Optional[str] = None
    cost_estimate_usd: Optional[float] = None
    depends_on: Optional[int] = None
    repo: Optional[str] = None
    base: Optional[str] = None


class StartBody(BaseModel):
    model: str = "vellur-coder"


class ReturnBody(BaseModel):
    note: str = ""


def register(app: FastAPI, ctrl: Controller, router: Router, agent: AgentManager, admin, admin_write) -> None:
    db = ctrl.db

    def agent_err(e) -> JSONResponse:
        details = getattr(e, "data", None)
        body = {"error": getattr(e, "code", "invalid"), "message": getattr(e, "message", str(e))}
        if details:
            body["details"] = details
        return JSONResponse(status_code=getattr(e, "status", 400), content=body)

    def get_item(mid: int) -> Optional[dict]:
        return db.one("SELECT * FROM mission_items WHERE id=?", (mid,))

    def active_objective() -> Optional[dict]:
        return db.one("SELECT * FROM mission_objectives WHERE state='active'")

    def task_of(item: dict) -> Optional[dict]:
        return agent.task(item["agent_task_id"]) if item["agent_task_id"] else None

    def item_out(item: dict) -> dict:
        return public_item(item, task_of(item))

    def snapshot() -> dict:
        obj = active_objective()
        items = [item_out(r) for r in db.all("SELECT * FROM mission_items ORDER BY position, id")]
        parked = db.all("SELECT id,title,state,created,updated FROM mission_objectives WHERE state='parked' "
                        "ORDER BY updated DESC LIMIT 10")
        # next eligible: ready first, then backlog; an unmet dependency (not verified) blocks the item
        rows = db.all(f"SELECT * FROM mission_items WHERE objective_id={obj['id']} ORDER BY position, id") if obj else []
        by_id = {r["id"]: r for r in rows}
        eff = {r["id"]: effective_state(r, task_of(r)) for r in rows}

        def eligible(r: dict) -> bool:
            dep = r["depends_on"]
            return dep is None or (dep in by_id and eff[dep] == "verified")

        nxt = next((r for s in ("ready", "backlog") for r in rows if eff[r["id"]] == s and eligible(r)), None)
        rows_all = db.all("SELECT * FROM mission_items" + (f" WHERE objective_id={obj['id']}" if obj else ""))
        estimate = sum(float(r["cost_estimate_usd"]) for r in rows_all if r["cost_estimate_usd"] is not None
                       and r["state"] != "verified")
        spent = 0.0
        for r in rows_all:
            t = task_of(r)
            if t:
                spent += float(t["cost_usd"] or 0)
        g = ctrl.budget.status(ctrl.settings.get(), None, ctrl.clock())
        ok, why = agent.available()
        running = next((t for t in agent.list() if t["state"] not in FINAL), None)
        return {"objective": obj, "parked_objectives": parked, "items": items,
                "next": public_item(nxt, task_of(nxt)) if nxt else None,
                "agent": {"available": ok, "why": why,
                          "running": {"id": running["id"], "state": running["state"], "title": running["title"],
                                      "repo": running["repo"], "cost_usd": running["cost_usd"]} if running else None},
                "budget": {"objective_estimate_usd": round(estimate, 4), "objective_spent_usd": round(spent, 4),
                           "global": {"month": g["month"], "budget_usd": g["budget_usd"], "total_usd": g["total_usd"],
                                      "remaining_usd": g["remaining_usd"], "percent": g["percent"],
                                      "exhausted": g["exhausted"]}}}

    # ── objective ──────────────────────────────────────────────────────────────────────────────
    @app.get("/autopilot/api/mission", dependencies=[Depends(admin)])
    async def mission():
        return snapshot()

    @app.post("/autopilot/api/mission/objective", dependencies=[Depends(admin_write)])
    async def set_objective(body: ObjectiveBody):
        title, detail = (body.title or "").strip(), (body.detail or "").strip()[:2000]
        if not 3 <= len(title) <= 200:
            return err("invalid", "Titlul obiectivului: 3–200 de caractere.")
        now = time.time()
        if body.id:
            obj = db.one("SELECT * FROM mission_objectives WHERE id=?", (body.id,))
            if not obj:
                return err("not_found", "Obiectiv necunoscut.")
            if obj["state"] == "active":
                return err("invalid", "Acest obiectiv este deja activ.")
            db.x("UPDATE mission_objectives SET state='parked', updated=? WHERE state='active'", (now,))
            db.x("UPDATE mission_objectives SET state='active', updated=? WHERE id=?", (now, body.id))
            db.event("info", "mission", f"objective {body.id} reactivated: {title[:80]}")
            oid = body.id
        else:
            db.x("UPDATE mission_objectives SET state='parked', updated=? WHERE state='active'", (now,))
            cur = db.x("INSERT INTO mission_objectives(title,detail,state,cost_estimate_usd,created,updated) "
                       "VALUES(?,?,?,?,?,?)", (title, detail, "active", body.cost_estimate_usd, now, now))
            db.event("info", "mission", f"objective {cur.lastrowid} set: {title[:80]}")
            oid = cur.lastrowid
        return db.one("SELECT * FROM mission_objectives WHERE id=?", (oid,))

    @app.post("/autopilot/api/mission/objective/park", dependencies=[Depends(admin_write)])
    async def park_objective():
        cur = db.x("UPDATE mission_objectives SET state='parked', updated=? WHERE state='active'", (time.time(),))
        if not cur.rowcount:
            return err("not_found", "Nu există un obiectiv activ.")
        db.event("info", "mission", "active objective parked")
        return {"ok": True}

    # ── items ───────────────────────────────────────────────────────────────────────────────────
    @app.post("/autopilot/api/mission/items", dependencies=[Depends(admin_write)])
    async def new_item(body: ItemBody):
        title = (body.title or "").strip()
        if not 1 <= len(title) <= 200:
            return err("invalid", "Titlul: 1–200 de caractere.")
        if body.state not in DIRECT_STATES:
            return err("invalid", "O sarcină nouă poate fi doar ideă, backlog sau pregătită.")
        if body.objective_id and not db.one("SELECT id FROM mission_objectives WHERE id=?", (body.objective_id,)):
            return err("not_found", "Obiectiv necunoscut.")
        if body.depends_on and not db.one("SELECT id FROM mission_items WHERE id=?", (body.depends_on,)):
            return err("not_found", "Sarcina de care depinde nu există.")
        if (body.cost_estimate_usd is not None and body.cost_estimate_usd < 0) or \
           (body.cost_estimate_usd is not None and body.cost_estimate_usd > 10000):
            return err("invalid", "Estimarea de cost nu este plauzibilă.")
        # without an explicit objective, new work belongs to the active one (the inbox is the objective's inbox)
        a = active_objective()
        obj_id = body.objective_id if body.objective_id else (a["id"] if a else None)
        now = time.time()
        pos = db.one("SELECT COALESCE(MAX(position) + 100, 1000) AS p FROM mission_items")["p"]
        cur = db.x("INSERT INTO mission_items(objective_id,title,detail,state,plan,repo,base,cost_estimate_usd,depends_on,"
                   "position,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (obj_id, title, (body.detail or "").strip()[:4000], body.state,
                    (body.plan or "").strip()[:8000], (body.repo or "").strip(), (body.base or "").strip(),
                    body.cost_estimate_usd, body.depends_on, pos, now, now))
        db.event("info", "mission", f"item {cur.lastrowid} added ({body.state}): {title[:80]}")
        return get_item(cur.lastrowid)

    @app.post("/autopilot/api/mission/items/{mid}", dependencies=[Depends(admin_write)])
    async def update_item(mid: int, body: ItemUpdate):
        item = get_item(mid)
        if not item:
            return err("not_found", "Sarcină necunoscută.")
        if effective_state(item, task_of(item)) == "running":
            return err("busy", "Agentul lucrează la această sarcină acum; așteaptă sau oprește-o din Projects.", 409)
        fields: dict = {}
        if body.title is not None:
            t = body.title.strip()
            if not 1 <= len(t) <= 200:
                return err("invalid", "Titlul: 1–200 de caractere.")
            fields["title"] = t
        if body.detail is not None:
            fields["detail"] = body.detail.strip()[:4000]
        if body.plan is not None:
            fields["plan"] = body.plan.strip()[:8000]
        if body.blocked_reason is not None:
            fields["blocked_reason"] = body.blocked_reason.strip()[:500]
        if body.repo is not None:
            fields["repo"] = body.repo.strip()
        if body.base is not None:
            fields["base"] = body.base.strip()
        if body.cost_estimate_usd is not None:
            if body.cost_estimate_usd < 0 or body.cost_estimate_usd > 10000:
                return err("invalid", "Estimarea de cost nu este plauzibilă.")
            fields["cost_estimate_usd"] = body.cost_estimate_usd
        if body.depends_on is not None:
            if body.depends_on and not db.one("SELECT id FROM mission_items WHERE id=?", (body.depends_on,)):
                return err("not_found", "Sarcina de care depinde nu există.")
            fields["depends_on"] = body.depends_on
        if body.state is not None:
            if body.state not in DIRECT_STATES:
                return err("invalid", "Starea nu poate fi setată manual aici.")
            fields["state"] = body.state
        if not fields:
            return err("invalid", "Nimic de schimbat.")
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        db.x(f"UPDATE mission_items SET {cols} WHERE id=?", tuple(fields.values()) + (mid,))
        db.event("info", "mission", f"item {mid} updated" + (f" → {fields['state']}" if "state" in fields else ""))
        return get_item(mid)

    @app.post("/autopilot/api/mission/items/{mid}/start", dependencies=[Depends(admin_write)])
    async def start_item(mid: int, body: StartBody):
        item = get_item(mid)
        if not item:
            return err("not_found", "Sarcină necunoscută.")
        est = effective_state(item, task_of(item))
        if est not in ("idea", "backlog", "ready"):
            return err("invalid", f"O sarcină în stare „{est}” nu poate fi pornită de aici; deschide-o în Projects.")
        if body.model not in ROLES:
            return err("invalid", "Model necunoscut.")
        obj = active_objective()
        parts = []
        if obj:
            parts.append(f"Obiectiv: {obj['title']}")
            if obj["detail"]:
                parts.append(obj["detail"])
            parts.append("")
        parts.append(f"Sarcină: {item['title']}")
        if item["detail"]:
            parts.append(item["detail"])
        if item["plan"]:
            parts += ["", "Plan (urmează acești pași):", item["plan"]]
        if item["blocked_reason"]:
            parts += ["", f"Obstacol cunoscut dintr-o rulare anterioară: {item['blocked_reason']}"]
        prompt = "\n".join(parts).strip()
        if not 5 <= len(prompt) <= 20000:
            return err("invalid", "Descrierea nu este suficientă pentru o sarcină de agent (5–20000 de caractere).")
        try:
            task = await agent.create(prompt, body.model, repo=item["repo"], base=item["base"])
        except AgentError as e:
            return agent_err(e)
        now = time.time()
        db.x("UPDATE mission_items SET state='running', agent_task_id=?, blocked_reason='', updated=? WHERE id=?",
             (task["id"], now, mid))
        db.event("info", "mission", f"item {mid} started as agent task {task['id']}", repo=item["repo"])
        return {"ok": True, "agent_task_id": task["id"], "item": item_out(get_item(mid))}

    @app.post("/autopilot/api/mission/items/{mid}/verify", dependencies=[Depends(admin_write)])
    async def verify_item(mid: int):
        item = get_item(mid)
        if not item:
            return err("not_found", "Sarcină necunoscută.")
        if effective_state(item, task_of(item)) != "review":
            return err("invalid", "Doar o sarcină a cărei rulare s-a terminat poate fi verificată.")
        db.x("UPDATE mission_items SET state='verified', blocked_reason='', updated=? WHERE id=?", (time.time(), mid))
        db.event("info", "mission", f"item {mid} verified as done by the owner",
                 agent_task_id=item["agent_task_id"])
        return item_out(get_item(mid))

    @app.post("/autopilot/api/mission/items/{mid}/return", dependencies=[Depends(admin_write)])
    async def return_item(mid: int, body: ReturnBody):
        item = get_item(mid)
        if not item:
            return err("not_found", "Sarcină necunoscută.")
        if effective_state(item, task_of(item)) != "review":
            return err("invalid", "Doar o sarcină în review poate fi trimisă înapoi în backlog.")
        note = (body.note or "").strip()[:500]
        db.x("UPDATE mission_items SET state='backlog', blocked_reason=?, updated=? WHERE id=?", (note, time.time(), mid))
        db.event("info", "mission", f"item {mid} returned to backlog" + (f": {note[:120]}" if note else ""))
        return item_out(get_item(mid))

    @app.post("/autopilot/api/mission/items/{mid}/suggest-plan", dependencies=[Depends(admin_write)])
    async def suggest_plan(mid: int):
        """Explicit owner action: ask the chat model to draft the plan. Costs are recorded like any other request."""
        item = get_item(mid)
        if not item:
            return err("not_found", "Sarcină necunoscută.")
        obj = active_objective()
        user = []
        if obj:
            user.append(f"Obiectiv: {obj['title']}")
            if obj["detail"]:
                user.append(obj["detail"])
        user.append(f"Sarcină: {item['title']}")
        if item["detail"]:
            user.append(item["detail"])
        if item["blocked_reason"]:
            user.append(f"Obstacol cunoscut: {item['blocked_reason']}")
        tag = f"mission:{mid}"
        started = ctrl.clock()
        resp = await router.handle("POST", "chat/completions", "", {},
                                   json.dumps({"model": "vellur-chat", "stream": False, "max_tokens": 700,
                                               "messages": [{"role": "system", "content": PLAN_SYSTEM},
                                                            {"role": "user", "content": "\n".join(user)}]}).encode(), tag)
        data = b"".join([c async for c in resp.stream]) if resp.stream is not None else (resp.content or b"")
        if resp.status != 200:
            try:
                msg = json.loads(data)["error"]["message"]
            except Exception:  # noqa: BLE001
                msg = data.decode(errors="replace")[:300]
            return JSONResponse(status_code=502, content={"error": "model", "message": msg})
        try:
            plan = (json.loads(data)["choices"][0]["message"].get("content") or "").strip()
        except (ValueError, KeyError, IndexError, TypeError):
            return JSONResponse(status_code=502, content={"error": "model", "message": "răspunsul modelului nu se poate citi"})
        if not plan:
            return err("model", "Modelul nu a returnat un plan.")
        db.x("UPDATE mission_items SET plan=?, updated=? WHERE id=?", (plan[:8000], time.time(), mid))
        spent = db.one("SELECT cost_usd, cost_source FROM usage WHERE tag=? AND ts>=? ORDER BY id DESC LIMIT 1",
                       (tag, started)) or {}
        db.event("info", "mission", f"item {mid}: plan drafted by the model")
        return {"plan": plan[:8000], "cost_usd": round(float(spent.get("cost_usd") or 0), 4),
                "cost_source": spent.get("cost_source") or ""}
