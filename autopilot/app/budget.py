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

    def session_estimate(self, s: dict, settings: dict, now: Optional[float] = None) -> float:
        now = now or time.time()
        h = session_hours(s, now)
        if s["provider"] == "hyperai":
            # Included hours first, then the advertised hourly rate. Hours are attributed in session order.
            before = sum(session_hours(r, now) for r in self.db.all(
                "SELECT * FROM sessions WHERE provider='hyperai' AND created_at>=? AND id<?",
                (month_start(s["created_at"]), s["id"])))
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
        estimated = sum(self.session_estimate(s, settings, now) for s in unreconciled)
        total = confirmed + estimated
        budget = float(settings["monthly_budget_usd"])
        cur_est, rate = 0.0, 0.0
        if current and not current.get("ended_at") and not current.get("dry_run"):
            cur_est = self.session_estimate(current, settings, now)
            rate = self.current_rate(current, settings, now)
        projected = total + rate * float(settings["idle_timeout_min"]) / 60.0
        remaining = budget - total
        return {
            "month": m,
            "budget_usd": round(budget, 2),
            "confirmed_usd": round(confirmed, 2),
            "estimated_unconfirmed_usd": round(estimated, 2),
            "total_usd": round(total, 2),
            "remaining_usd": round(remaining, 2),
            "percent": round(100.0 * total / budget, 1) if budget > 0 else 100.0,
            "current_session_estimate_usd": round(cur_est, 2),
            "current_rate_usd_per_hour": round(rate, 3),
            "projected_if_idle_out_usd": round(projected, 2),
            "hours_left_at_current_rate": round(remaining / rate, 1) if rate > 0 and remaining > 0 else None,
            "hyperai_hours_used_estimate": round(self.hyperai_hours_used(now), 2),
            "hyperai_hours_left_estimate": round(max(0.0, float(settings["hyperai_included_hours"]) - self.hyperai_hours_used(now)), 2),
            "exhausted": budget <= 0 or total >= budget,
            "note": "Estimates are not confirmed charges. Confirmed = provider-reported.",
        }

    def current_rate(self, s: dict, settings: dict, now: float) -> float:
        if s["provider"] == "hyperai":
            return float(settings["hyperai_hourly_after_usd"]) if self.hyperai_hours_used(now) >= float(settings["hyperai_included_hours"]) else 0.0
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
        if st["total_usd"] + rate * MIN_SESSION_HOURS + extra_cost > float(settings["monthly_budget_usd"]) + 1e-9:
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
