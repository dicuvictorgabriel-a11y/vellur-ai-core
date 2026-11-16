"""Budget engine. Estimates are always labelled as estimates; only provider-reported charges are "confirmed".

Monthly supplemental GPU budget (independent of the VPS). Ledger rows of kind "provider_charge" are confirmed;
sessions not yet reconciled contribute their live estimate. A local controller cannot enforce a strict cap if a
provider keeps billing, reports late or becomes unreachable: see docs/AUTOPILOT.md.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from .db import DB

THRESHOLDS = (50, 75, 90, 100)
MIN_SESSION_HOURS = 0.5  # a start is only allowed if at least this much runtime fits in the budget


def month_key(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).strftime("%Y-%m")


def month_start(ts: Optional[float] = None) -> float:
    d = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp()


def session_hours(s: dict, now: float) -> float:
    start = s.get("billing_started_at")
    if not start or s.get("dry_run"):
        return 0.0
    end = s.get("ended_at") or now
    return max(0.0, (end - start) / 3600.0)


class Budget:
    def __init__(self, db: DB):
        self.db = db

    # ── HyperAI subscription hours (estimated from our own session records) ──────────────────────────
    def hyperai_hours_used(self, now: Optional[float] = None) -> float:
        now = now or time.time()
        rows = self.db.all("SELECT * FROM sessions WHERE provider='hyperai' AND created_at>=?", (month_start(now),))
        return sum(session_hours(r, now) for r in rows)

    def _hyperai_hours_before(self, since: float, now: float) -> dict:
        """{session id: HyperAI hours used by earlier sessions since `since`}, from ONE query (running total).
        Replaces a query per session, which made Budget.status quadratic in the number of HyperAI sessions."""
        out, total = {}, 0.0
        for r in self.db.all("SELECT id, billing_started_at, ended_at, dry_run FROM sessions "
                             "WHERE provider='hyperai' AND created_at>=? ORDER BY id", (since,)):
            out[r["id"]] = total
            total += session_hours(r, now)
        out[None] = total            # hours of the whole month
        return out

    def session_estimate(self, s: dict, settings: dict, now: Optional[float] = None,
                         hyperai_before: Optional[dict] = None) -> float:
        now = now or time.time()
        h = session_hours(s, now)
        if s["provider"] == "hyperai":
            # Included hours first, then the advertised hourly rate. Hours are attributed in session order.
            if hyperai_before is None or s["id"] not in hyperai_before:
                hyperai_before = self._hyperai_hours_before(month_start(s["created_at"]), now)
            before = hyperai_before.get(s["id"], hyperai_before[None])
            included_left = max(0.0, float(settings["hyperai_included_hours"]) - before)
            billable = max(0.0, h - included_left)
            return billable * float(settings["hyperai_hourly_after_usd"])
        return h * float(s.get("dph") or 0) + (float(s.get("extra_cost") or 0) if h > 0 else 0.0)

    def status(self, settings: dict, current: Optional[dict] = None, now: Optional[float] = None) -> dict:
        now = now or time.time()
        m = month_key(now)
        confirmed = sum(r["amount_usd"] for r in self.db.all(
            "SELECT amount_usd FROM ledger WHERE month=? AND kind='provider_charge'", (m,)))
        unreconciled = self.db.all(
            "SELECT * FROM sessions WHERE created_at>=? AND reconciled=0 AND dry_run=0", (month_start(now),))
        hyperai_before = self._hyperai_hours_before(month_start(now), now)       # one query for all sessions below
        estimated = sum(self.session_estimate(s, settings, now, hyperai_before) for s in unreconciled)
        # Model APIs: per-request cost reported by the provider vs. our own token × price estimate.
        api = {r["cost_source"]: float(r["c"] or 0) for r in self.db.all(
            "SELECT cost_source, SUM(cost_usd) AS c FROM usage WHERE month=? GROUP BY cost_source", (m,))}
        api_reported, api_estimated = api.get("provider", 0.0), api.get("estimate", 0.0)
        # Fixed monthly costs: provider subscriptions always count; listed fixed costs only if marked in_budget.
        subs = sum(float(p.get("monthly_usd") or 0) for p in (self.db.kv_get("llm_providers", {}) or {}).values()
                   if p.get("plan") == "subscription" and p.get("enabled", True))
        fixed = settings.get("fixed_costs") or []
        fixed_in = sum(float(f["usd"]) for f in fixed if f.get("in_budget"))
        fixed_out = sum(float(f["usd"]) for f in fixed if not f.get("in_budget"))
        total = confirmed + estimated + api_reported + api_estimated + subs + fixed_in
        budget = float(settings["monthly_budget_usd"])
        total_cap = float(settings.get("total_budget_cap_usd") or 0.0)
        all_ledger = float((self.db.one("SELECT SUM(amount_usd) AS c FROM ledger WHERE kind='provider_charge'") or {}).get("c") or 0.0)
        all_api = float((self.db.one("SELECT SUM(cost_usd) AS c FROM usage WHERE cost_source IN ('provider','estimate')") or {}).get("c") or 0.0)
        older_est = 0.0
        if total_cap > 0:
            older_unreconciled = self.db.all("SELECT * FROM sessions WHERE created_at<? AND reconciled=0 AND dry_run=0", (month_start(now),))
            older_est = sum(self.session_estimate(s, settings, now) for s in older_unreconciled)
        cumulative = all_ledger + all_api + estimated + older_est + subs + fixed_in
        cur_est, rate = 0.0, 0.0
        hyperai_used = hyperai_before[None]
        if current and not current.get("ended_at") and not current.get("dry_run"):
            cur_est = self.session_estimate(current, settings, now, hyperai_before)
            rate = self.current_rate(current, settings, now, hyperai_used)
        projected = total + rate * float(settings["idle_timeout_min"]) / 60.0
        monthly_remaining = budget - total
        cap_remaining = (total_cap - cumulative) if total_cap > 0 else None
        remaining = min(monthly_remaining, cap_remaining) if cap_remaining is not None else monthly_remaining
        cap_exhausted = total_cap > 0 and cumulative >= total_cap
        exhausted = budget <= 0 or total >= budget or cap_exhausted
        pct_month = (100.0 * total / budget) if budget > 0 else 100.0
        pct_cap = (100.0 * cumulative / total_cap) if total_cap > 0 else 0.0
        return {
            "month": m,
            "budget_usd": round(budget, 2),
            "total_cap_usd": round(total_cap, 2) if total_cap > 0 else 0.0,
            "cumulative_usd": round(cumulative, 4),
            "total_cap_remaining_usd": round(cap_remaining, 2) if cap_remaining is not None else None,
            "total_cap_exhausted": cap_exhausted,
            "confirmed_usd": round(confirmed, 2),
            "estimated_unconfirmed_usd": round(estimated, 2),
            "total_usd": round(total, 2),
            "remaining_usd": round(remaining, 2),
            "percent": round(max(pct_month, pct_cap), 1),
            "current_session_estimate_usd": round(cur_est, 2),
            "current_rate_usd_per_hour": round(rate, 3),
            "projected_if_idle_out_usd": round(projected, 2),
            "hours_left_at_current_rate": round(remaining / rate, 1) if rate > 0 and remaining > 0 else None,
            "hyperai_hours_used_estimate": round(hyperai_used, 2),
            "hyperai_hours_left_estimate": round(max(0.0, float(settings["hyperai_included_hours"]) - hyperai_used), 2),
            "api_reported_usd": round(api_reported, 4),
            "api_estimated_usd": round(api_estimated, 4),
            "subscriptions_usd": round(subs, 2),
            "fixed_in_budget_usd": round(fixed_in, 2),
            "fixed_outside_budget_usd": round(fixed_out, 2),
            "all_in_total_usd": round(total + fixed_out, 2),
            "exhausted": exhausted,
            "note": "Estimates are not confirmed charges. Confirmed = provider-reported.",
        }

    def current_rate(self, s: dict, settings: dict, now: float, hyperai_used: Optional[float] = None) -> float:
        if s["provider"] == "hyperai":
            used = self.hyperai_hours_used(now) if hyperai_used is None else hyperai_used
            return float(settings["hyperai_hourly_after_usd"]) if used >= float(settings["hyperai_included_hours"]) else 0.0
        return float(s.get("dph") or 0)

    def check_start(self, settings: dict, provider: str, dph: float, extra_cost: float,
                    now: Optional[float] = None) -> tuple[bool, str]:
        """May a new allocation at `dph` USD/h start? Includes one-off extras (e.g. model download traffic)."""
        st = self.status(settings, None, now)
        if st["exhausted"]:
            return False, "budget_exhausted"
        if provider == "hyperai":
            rate = 0.0 if st["hyperai_hours_left_estimate"] > MIN_SESSION_HOURS else float(settings["hyperai_hourly_after_usd"])
        else:
            rate = dph
        if rate > float(settings["max_hourly_usd"]) + 1e-9:
            return False, "price_above_limit"
        needed = rate * MIN_SESSION_HOURS + extra_cost
        if st["total_usd"] + needed > float(settings["monthly_budget_usd"]) + 1e-9:
            return False, "budget_insufficient"
        total_cap = float(settings.get("total_budget_cap_usd") or 0.0)
        if total_cap > 0 and st["cumulative_usd"] + needed > total_cap + 1e-9:
            return False, "budget_insufficient"
        return True, "ok"

    def crossed_thresholds(self, settings: dict, now: Optional[float] = None) -> list[int]:
        """Thresholds newly crossed this month (each reported once)."""
        st = self.status(settings, None, now)
        out = []
        for t in THRESHOLDS:
            if st["percent"] >= t and self.db.alert_once(st["month"], f"budget_{t}"):
                out.append(t)
        return out

    def record_provider_charge(self, provider: str, ref: str, amount: float, ts: float, note: str = "") -> bool:
        """Idempotent per (provider, ref). Returns True if new."""
        try:
            self.db.x("INSERT INTO ledger(ts,month,provider,kind,amount_usd,ref,note) VALUES(?,?,?,?,?,?,?)",
                      (ts, month_key(ts), provider, "provider_charge", float(amount), ref, note))
            return True
        except Exception:
            return False
