"""Dashboard-editable settings, validated and clamped by the server-side hard ceilings."""
from __future__ import annotations

from .config import DEFAULT_SETTINGS, PROFILES, Env
from .db import DB

PROVIDERS = ("hyperai", "vastai", "mock")


class SettingsError(ValueError):
    pass


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
        if "hyperai_ssh_host" in clean and not all(c.isalnum() or c in ".-" for c in clean["hyperai_ssh_host"]):
            raise SettingsError("invalid host name")
        self.db.settings_put(clean)
        return self.get()
