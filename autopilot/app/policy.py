"""Autonomous, capability-aware model routing (NOVA blueprint §19, owner decision 2026-11-16: choose automatically).

The model pool is the owner's curated set: every target saved in any route (Models → routing). Modes reorder that pool
for each request; they never add a provider the owner did not configure, so no new charge category appears by itself.

  auto      picks a policy per request: chat → economy, coder → balanced, max → quality, upgraded one level for large
            contexts or after a recent failure of this task.
  economy   cheapest expected cost (price × expected tokens ÷ success probability of the call).
  balanced  best observed task success per expected dollar.
  quality   best observed task success, then fewer errors, then the owner's route order; price is ignored.
  manual    exactly the saved route for the requested name (the behaviour before NOVA).

Hard rules: a model without a required capability (tools, vision, image output, enough context) is never chosen;
a paid model with no known price is never chosen automatically (its cost cannot be reserved); paid calls reserve their
expected cost atomically against the monthly *allowance* and the per-task limit before they are sent, and the
reservation is released when the call is recorded. Without an allowance, automatic modes fall back to manual routing:
NOVA does not start new automatic paid inference until the owner has set how much it may spend.

Quality evidence comes only from recorded outcomes (judged task outcomes and call errors). With little evidence every
model starts from the same neutral prior; nothing is invented.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

MODES = ("auto", "economy", "balanced", "quality", "manual")
AUTO_POLICY = {"vellur-chat": "economy", "vellur-coder": "balanced", "vellur-max": "quality", "vellur-image": "economy"}
UPGRADE = {"economy": "balanced", "balanced": "quality", "quality": "quality"}
OUT_TOKENS = {"vellur-chat": 800, "vellur-coder": 2000, "vellur-max": 3000, "vellur-image": 0}
MAX_ATTEMPTS = 3           # bounded retries: at most three targets per request in automatic modes
WINDOW = 30 * 86400        # evidence window
MIN_CALLS, MIN_TASKS = 5, 3   # evidence below this is treated as no evidence


def est_tokens(payload: dict) -> int:
    """Rough input size: 4 characters per token (only used to filter by context and to reserve budget)."""
    return max(1, len(json.dumps(payload.get("messages") or payload.get("prompt") or "", ensure_ascii=False)) // 4)


class Policy:
    def __init__(self, router):
        self.r = router
        self.db = router.db
        self._lock = threading.Lock()
        self._reserved: dict[int, tuple] = {}      # id -> (tag, target, usd)
        self._next = 0

    # ── configuration ───────────────────────────────────────────────────────────────────────────
    def config(self) -> dict:
        st = self.r.settings.get()
        mode = st.get("routing_mode") or "auto"
        allowance = float(st.get("auto_allowance_usd") or 0)
        return {"mode": mode if mode in MODES else "auto", "allowance_usd": allowance,
                "task_limit_usd": float(st.get("auto_task_limit_usd") or 0),
                "active": mode != "manual" and allowance > 0}

    def pool(self) -> list[str]:
        routes = self.r.settings.get()["routes"]
        seen: list[str] = []
        for role in ("vellur-coder", "vellur-max", "vellur-chat", "vellur-image"):
            for t in routes.get(role) or []:
                if t not in seen:
                    seen.append(t)
        return seen

    # ── evidence ────────────────────────────────────────────────────────────────────────────────
    def evidence(self, target: str) -> dict:
        since = time.time() - WINDOW
        u = self.db.one("SELECT COUNT(*) AS n, SUM(1-ok) AS bad, AVG(latency_ms) AS ms FROM usage WHERE target=? AND ts>=?",
                        (target, since)) or {}
        n, bad = int(u.get("n") or 0), int(u.get("bad") or 0)
        t = self.db.one("SELECT COUNT(*) AS n, SUM(CASE WHEN t.outcome='completed' THEN 1 ELSE 0 END) AS ok FROM tasks t "
                        "WHERE t.created>=? AND t.outcome<>'' AND EXISTS (SELECT 1 FROM usage u WHERE u.tag='task:'||t.id "
                        "AND u.target=?)", (since, target)) or {}
        tn, tok = int(t.get("n") or 0), int(t.get("ok") or 0)
        # Too little evidence is noise: below MIN_CALLS / MIN_TASKS every model keeps the same neutral prior.
        return {"calls": n, "errors": bad, "error_rate": (bad + 0.5) / (n + 5) if n >= MIN_CALLS else 0.1,
                "latency_ms": int(u.get("ms") or 0), "tasks": tn, "tasks_completed": tok,
                "success": (tok + 1) / (tn + 2) if tn >= MIN_TASKS else 0.5}

    def expected_usd(self, target: str, role: str, payload: dict) -> Optional[float]:
        info = self.r.info(target)
        if not info or info["kind"] == "gpu" or info["free"]:
            return 0.0
        if role == "vellur-image":
            p = self.r.image_price(target)
            return None if p is None else p * int(payload.get("n") or 1)
        pr = self.r.price(target)
        if not pr:
            return None
        out = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or OUT_TOKENS.get(role, 1500))
        return (est_tokens(payload) * pr[0] + out * pr[1]) / 1e6

    # ── planning ────────────────────────────────────────────────────────────────────────────────
    def required(self, role: str, capability: Optional[str], payload: dict) -> list[str]:
        req = [capability] if capability else []
        if payload.get("tools") and "tool_call" not in req:
            req.append("tool_call")
        return req

    def plan(self, role: str, capability: Optional[str], payload: dict, tag: str = "") -> tuple[list[str], dict]:
        cfg = self.config()
        req = self.required(role, capability, payload)
        if not cfg["active"]:
            why = ("Manual mode: the saved route for this name." if cfg["mode"] == "manual" else
                   "Automatic routing is waiting for a spending allowance; the saved route is used until one is set.")
            return self.r.chain(role, capability), {"mode": cfg["mode"], "policy": "manual", "required": req, "reason": why}
        policy = AUTO_POLICY.get(role, "balanced") if cfg["mode"] == "auto" else cfg["mode"]
        need_ctx = est_tokens(payload) + OUT_TOKENS.get(role, 1500)
        upgraded = ""
        if cfg["mode"] == "auto":
            recent = self.db.one("SELECT outcome FROM routing_decisions WHERE tag=? AND tag<>'' ORDER BY id DESC LIMIT 1", (tag,))
            if need_ctx > 30000:
                upgraded = "large context"
            elif recent and recent["outcome"] == "failed":
                upgraded = "the previous call for this work failed"
            if upgraded:
                policy = UPGRADE[policy]
        own = self.r.chain(role)
        routes = self.r.settings.get()["routes"]
        image_only = set(routes.get("vellur-image") or []) - {x for r, ts in routes.items() if r != "vellur-image" for x in ts}
        rows, rejected = [], []
        for t in self.pool():
            if role != "vellur-image" and t in image_only:
                continue          # a model saved only for images is not a text candidate
            caps = self.r.capabilities(t)
            missing = [c for c in req if c not in caps]
            if role == "vellur-image" and "image_generation" not in caps:
                missing = missing or ["image_generation"]
            if role != "vellur-image" and "image_generation" in caps and "chat" not in caps:
                missing = missing or ["text output"]
            if missing:
                rejected.append({"target": t, "why": "missing " + ", ".join(m.replace("_", " ") for m in missing)})
                continue
            ctx = self._ctx(t)
            if ctx and ctx < need_ctx:
                rejected.append({"target": t, "why": f"context {ctx} < {need_ctx} tokens needed"})
                continue
            usd = self.expected_usd(t, role, payload)
            if usd is None:
                rejected.append({"target": t, "why": "price unknown: its cost cannot be reserved"})
                continue
            ev = self.evidence(t)
            exp = usd / max(0.05, 1 - ev["error_rate"])          # expected cost including retries
            rows.append({"target": t, "usd": round(usd, 6), "expected_usd": round(exp, 6), **ev,
                         "own_rank": own.index(t) if t in own else 99})
        key = {"economy": lambda x: (x["expected_usd"], x["error_rate"], x["own_rank"]),
               "quality": lambda x: (-x["success"], x["error_rate"], x["own_rank"]),
               "balanced": lambda x: (-(x["success"] * (1 - x["error_rate"])) / (x["expected_usd"] + 0.002), x["own_rank"])}[policy]
        rows.sort(key=key)
        chosen = rows[0] if rows else None
        reason = self._reason(policy, cfg["mode"], chosen, rows, upgraded)
        return [x["target"] for x in rows][:MAX_ATTEMPTS], {"mode": cfg["mode"], "policy": policy, "required": req,
                                                           "reason": reason, "candidates": rows[:8], "rejected": rejected[:8]}

    def _ctx(self, target: str) -> int:
        name, _, model = target.partition(":")
        for m in (self.db.kv_get_shared(f"llm_catalog_{name}") or {}).get("models") or []:
            if m["id"] == model:
                return int(m.get("ctx") or 0)
        return 0

    @staticmethod
    def _reason(policy: str, mode: str, c: Optional[dict], rows: list, upgraded: str) -> str:
        if not c:
            return "No model in your pool has the required capabilities and a known price."
        why = {"economy": "lowest expected cost", "balanced": "best observed success per dollar",
               "quality": "best observed task success"}[policy]
        ev = (f"{c['tasks_completed']}/{c['tasks']} tasks completed, {c['errors']}/{c['calls']} failed calls in 30 days"
              if c["calls"] or c["tasks"] else "no history yet (neutral prior)")
        cost = "free" if c["usd"] == 0 else f"≈${c['usd']:.4f} for this request"
        pre = f"{mode.capitalize()} mode → {policy}" + (f" (upgraded: {upgraded})" if upgraded else "")
        return f"{pre}: {c['target']} has the {why} among {len(rows)} capable models; {cost}; {ev}."

    def log(self, tag: str, role: str, decision: dict, chosen: str) -> int:
        cand = decision.get("candidates") or []
        est = next((x["usd"] for x in cand if x["target"] == chosen), 0.0)
        cur = self.db.x("INSERT INTO routing_decisions(ts,tag,role,mode,policy,required,chosen,reason,candidates,est_usd) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), tag, role, decision["mode"], decision["policy"], json.dumps(decision["required"]),
                         chosen, decision["reason"], json.dumps({"ranked": cand, "rejected": decision.get("rejected", [])}), est))
        return int(cur.lastrowid or 0)

    def outcome(self, decision_id: int, outcome: str) -> None:
        if decision_id:
            self.db.x("UPDATE routing_decisions SET outcome=? WHERE id=?", (outcome, decision_id))

    # ── atomic budget reservation ───────────────────────────────────────────────────────────────
    def spent_month(self) -> float:
        from .budget import month_key
        r = self.db.one("SELECT SUM(cost_usd) AS c FROM usage WHERE month=? AND cost_source IN ('provider','estimate')",
                        (month_key(self.r.ctrl.clock()),))
        return float((r or {}).get("c") or 0)

    def reserve(self, tag: str, target: str, usd: float, auto: bool = True) -> tuple[Optional[int], str]:
        """Reserve `usd` for one paid call. Returns (id, "") or (None, why). Free/GPU calls need no reservation."""
        info = self.r.info(target)
        if not info or info["kind"] == "gpu" or info["free"]:
            return 0, ""
        cfg = self.config()
        st = self.r.settings.get()
        with self._lock:
            open_total = sum(v[2] for v in self._reserved.values())
            b = self.r.ctrl.budget.status(st, None, self.r.ctrl.clock())
            if b["exhausted"] or open_total + max(0.0, usd) > float(b["remaining_usd"]) + 1e-9:
                cap_note = (f" (total cap ${b['total_cap_usd']:.2f})" if b.get("total_cap_usd") else "")
                return None, f"budget limit reached{cap_note}: ${max(0.0, float(b['remaining_usd']) - open_total):.4f} available"
            if auto:
                if usd <= 0:
                    return 0, ""
                if self.spent_month() + open_total + usd > cfg["allowance_usd"]:
                    return None, (f"would exceed the automatic spending allowance (${cfg['allowance_usd']:.2f}/month); "
                                  "raise it in Models → Routing or approve a manual choice")
                if tag.startswith("task:") and cfg["task_limit_usd"] > 0:
                    task_open = sum(v[2] for v in self._reserved.values() if v[0] == tag)
                    if self.r.tag_cost(tag) + task_open + usd > cfg["task_limit_usd"]:
                        return None, f"would exceed the per-task limit (${cfg['task_limit_usd']:.2f})"
            if tag.startswith("task:"):
                run_limit = float(st.get("agent_max_usd") or 0.0)
                if run_limit > 0:
                    tid = tag.split(":", 1)[1]
                    run_meta = self.db.kv_get(f"task_run_{tid}") or {} if tid.isdigit() else {}
                    cost0 = float(run_meta.get("cost0") or 0.0)
                    task_open = sum(v[2] for v in self._reserved.values() if v[0] == tag)
                    if self.r.tag_cost(tag) - cost0 + task_open + max(0.0, usd) > run_limit + 1e-9:
                        return None, f"would exceed the per-run agent limit (${run_limit:.2f})"
            if usd <= 0:
                return 0, ""
            self._next += 1
            self._reserved[self._next] = (tag, target, usd)
            return self._next, ""

    def release(self, rid: Optional[int]) -> None:
        if rid:
            with self._lock:
                self._reserved.pop(rid, None)

    def status(self) -> dict:
        cfg = self.config()
        with self._lock:
            open_total = round(sum(v[2] for v in self._reserved.values()), 6)
            open_n = len(self._reserved)
        spent = round(self.spent_month(), 4)
        return {**cfg, "modes": list(MODES), "pool": self.pool(), "spent_month_usd": spent, "reserved_usd": open_total,
                "reserved_calls": open_n, "available_usd": round(max(0.0, cfg["allowance_usd"] - spent - open_total), 4)}

    def explain(self, tag: str = "", decision_id: int = 0) -> Optional[dict]:
        row = (self.db.one("SELECT * FROM routing_decisions WHERE id=?", (decision_id,)) if decision_id else
               self.db.one("SELECT * FROM routing_decisions WHERE tag=? ORDER BY id DESC LIMIT 1", (tag,)))
        if not row:
            return None
        data = json.loads(row["candidates"] or "{}")
        ranked = data.get("ranked") or []
        chosen = next((x for x in ranked if x["target"] == row["chosen"]), None)
        cheaper = None
        if chosen:
            alts = [x for x in ranked if x["target"] != row["chosen"] and x["usd"] < chosen["usd"]]
            if alts:
                a = min(alts, key=lambda x: x["usd"])
                cheaper = {**a, "note": (f"{a['target']} would cost ≈${a['usd']:.4f} instead of ≈${chosen['usd']:.4f}; "
                                         f"its record: {a['tasks_completed']}/{a['tasks']} tasks completed, "
                                         f"{a['errors']}/{a['calls']} failed calls." if a["tasks"] or a["calls"] else
                                         f"{a['target']} would cost ≈${a['usd']:.4f}; it has no recorded outcomes yet, "
                                         "so it is unknown whether it would have completed this work.")}
        return {"id": row["id"], "ts": row["ts"], "tag": row["tag"], "role": row["role"], "mode": row["mode"],
                "policy": row["policy"], "required": json.loads(row["required"] or "[]"), "chosen": row["chosen"],
                "reason": row["reason"], "est_usd": row["est_usd"], "outcome": row["outcome"], "ranked": ranked,
                "rejected": data.get("rejected") or [], "cheaper": cheaper}
