"""Dashboard-editable settings, validated and clamped by the server-side hard ceilings."""
from __future__ import annotations

import re

from .config import DEFAULT_SETTINGS, PROFILES, ROLES, Env
from .db import DB

PROVIDERS = ("hyperai", "vastai", "mock")


class SettingsError(ValueError):
    pass


TARGET_RE = re.compile(r"^(gpu|[a-z0-9][a-z0-9-]{1,23}:[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199})$")


def _routes(v) -> dict:
    if not isinstance(v, dict):
        raise SettingsError("routes must be an object")
    out = {}
    for role, targets in v.items():
        if role not in ROLES:
            raise SettingsError(f"unknown model name: {role}")
        if not isinstance(targets, list) or len(targets) > 6:
            raise SettingsError(f"{role}: up to 6 targets")
        for t in targets:
            if not isinstance(t, str) or not TARGET_RE.match(t):
                raise SettingsError(f"{role}: invalid target {t!r} (use provider:model-id or gpu)")
        out[role] = list(dict.fromkeys(targets))
    return {**{r: [] for r in ROLES}, **out}


def _prices(v) -> dict:
    if not isinstance(v, dict) or len(v) > 200:
        raise SettingsError("prices must be an object")
    out = {}
    for t, pair in v.items():
        if not TARGET_RE.match(str(t)) or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise SettingsError(f"invalid price entry for {t!r} (expected [input, output] USD per million tokens)")
        try:
            a, b = float(pair[0]), float(pair[1])
        except (TypeError, ValueError):
            raise SettingsError(f"invalid price for {t!r}")
        if not (0 <= a <= 1000 and 0 <= b <= 1000):
            raise SettingsError(f"price out of range for {t!r}")
        out[str(t)] = [a, b]
    return out


def _fixed_costs(v) -> list:
    if not isinstance(v, list) or len(v) > 20:
        raise SettingsError("fixed costs must be a list (max 20)")
    out = []
    for row in v:
        try:
            name = str(row["name"]).strip()[:60]
            usd = float(row.get("usd", 0))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SettingsError("each fixed cost needs a name and a monthly USD amount")
        if not name or not 0 <= usd <= 100000:
            raise SettingsError("invalid fixed cost")
        out.append({"name": name, "usd": usd, "in_budget": bool(row.get("in_budget", False))})
    return out


STRUCTURED = {"routes": _routes, "prices": _prices, "fixed_costs": _fixed_costs}


class Settings:
    def __init__(self, db: DB, env: Env):
        self.db, self.env = db, env

    def get(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        s.update(self.db.settings_get())
        # Ceilings always win, even over stored values.
        s["monthly_budget_usd"] = min(float(s["monthly_budget_usd"]), self.env.hard_max_monthly_usd)
        s["max_hourly_usd"] = min(float(s["max_hourly_usd"]), self.env.hard_max_hourly_usd)
        return s

    def update(self, changes: dict) -> dict:
        clean: dict = {}
        for k, v in changes.items():
            if k not in DEFAULT_SETTINGS:
                raise SettingsError(f"unknown setting: {k}")
            d = DEFAULT_SETTINGS[k]
            if k in STRUCTURED:
                clean[k] = STRUCTURED[k](v)
                continue
            if k == "agent_auto_publish_main_repo":
                if not isinstance(v, str) or (v and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}", v)):
                    raise SettingsError("agent_auto_publish_main_repo must be empty or an exact owner/name")
                if v and v.split("/", 1)[1] in (".", ".."):
                    raise SettingsError("invalid repository name")
                clean[k] = v
                continue
            try:
                if isinstance(d, bool):
                    if not isinstance(v, bool):
                        raise TypeError
                    clean[k] = v
                elif isinstance(d, int) and not isinstance(d, bool):
                    clean[k] = int(v)
                elif isinstance(d, float):
                    clean[k] = float(v)
                else:
                    clean[k] = str(v).strip()
            except (TypeError, ValueError):
                raise SettingsError(f"invalid value for {k}")
        rules = {
            "max_session_usd": (0.1, 1000), "agent_max_minutes": (1, 240), "agent_max_usd": (0, 1000),
            "idle_timeout_min": (5, 240), "max_session_hours": (0.25, 24), "hyperai_ssh_port": (0, 65535),
            "hyperai_included_hours": (0, 1000), "hyperai_hourly_after_usd": (0, 10),
            "vast_min_reliability": (0.5, 1.0), "vast_disk_gb": (30, 500),
        }
        for k, (lo, hi) in rules.items():
            if k in clean and not (lo <= clean[k] <= hi):
                raise SettingsError(f"{k} must be between {lo} and {hi}")
        if "monthly_budget_usd" in clean:
            if not 0 <= clean["monthly_budget_usd"] <= self.env.hard_max_monthly_usd:
                raise SettingsError(f"monthly budget must be 0–{self.env.hard_max_monthly_usd:g} USD "
                                    "(the ceiling is changed only on the server)")
        if "max_hourly_usd" in clean:
            if not 0 <= clean["max_hourly_usd"] <= self.env.hard_max_hourly_usd:
                raise SettingsError(f"max hourly rate must be 0–{self.env.hard_max_hourly_usd:g} USD "
                                    "(the ceiling is changed only on the server)")
        if "preferred_provider" in clean and clean["preferred_provider"] not in PROVIDERS:
            raise SettingsError("unknown provider")
        if "profile" in clean and clean["profile"] not in PROFILES:
            raise SettingsError("unknown profile")
        if "vast_idle_action" in clean and clean["vast_idle_action"] not in ("destroy", "stop"):
            raise SettingsError("vast_idle_action must be destroy or stop")
        if "memory" in clean and len(clean["memory"]) > 8000:
            raise SettingsError("project memory is limited to 8000 characters")
        if "hyperai_ssh_host" in clean and not all(c.isalnum() or c in ".-" for c in clean["hyperai_ssh_host"]):
            raise SettingsError("invalid host name")
        self.db.settings_put(clean)
        return self.get()
