"""Dashboard login: one admin password (PBKDF2-SHA256, stdlib), cookie sessions, forced change of temporary passwords,
brute-force throttling. No browser basic-auth popup.

  * A temporary password is set on the server (`sudo vac autopilot password [temp]`) and must be changed at first login.
  * Sessions: random 256-bit token in an HttpOnly, Secure, SameSite=Strict cookie scoped to /autopilot/, 30 days; only
    its SHA-256 is stored. Changing or resetting the password revokes all sessions.
  * Throttling: 5 failures per client IP per 15 min, 30 failures in total per 15 min → 429.
"""
from __future__ import annotations

import asyncio
import os
import hashlib
import hmac
import secrets
import time
from typing import Optional

from .db import DB

COOKIE = "vellur_ap"
SESSION_SECONDS = 30 * 24 * 3600
ITERATIONS = 400_000
MIN_LEN = 12
WINDOW = 15 * 60
PER_IP_FAILS = 5
TOTAL_FAILS = 30


def _hash(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations).hex()


def temp_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Auth:
    def __init__(self, db: DB, clock=time.time):
        self.db, self.clock = db, clock
        self._fails: dict[str, list[float]] = {}

    # ── password ─────────────────────────────────────────────────────────────────────────────────
    def has_password(self) -> bool:
        return bool(self.db.kv_get("admin_password"))

    def set_password(self, password: str, must_change: bool) -> None:
        salt = secrets.token_bytes(16)
        self.db.kv_set("admin_password", {"salt": salt.hex(), "hash": _hash(password, salt), "iter": ITERATIONS,
                                          "must_change": must_change, "updated": self.clock()})
        self.db.kv_set("admin_sessions", {})          # revoke every session
        self.db.event("alert" if must_change else "info", "auth",
                      "temporary dashboard password set (must be changed)" if must_change else "dashboard password changed")

    def must_change(self) -> bool:
        rec = self.db.kv_get("admin_password") or {}
        return bool(rec.get("must_change"))

    def _check(self, password: str) -> bool:
        rec = self.db.kv_get("admin_password")
        if not rec:
            return False
        got = _hash(password, bytes.fromhex(rec["salt"]), int(rec["iter"]))
        return hmac.compare_digest(got, rec["hash"])

    # ── throttling ───────────────────────────────────────────────────────────────────────────────
    def _recent(self, key: str) -> list[float]:
        now = self.clock()
        lst = [t for t in self._fails.get(key, []) if now - t < WINDOW]
        self._fails[key] = lst
        return lst

    def _throttled(self, ip: str) -> bool:
        return len(self._recent(ip)) >= PER_IP_FAILS or len(self._recent("*")) >= TOTAL_FAILS

    def _fail(self, ip: str) -> None:
        now = self.clock()
        self._fails.setdefault(ip, []).append(now)
        self._fails.setdefault("*", []).append(now)

    # ── sessions ─────────────────────────────────────────────────────────────────────────────────
    async def login(self, password: str, ip: str) -> str:
        if self._throttled(ip):
            raise AuthError(429, "throttled", "Too many failed attempts. Wait 15 minutes.")
        ok = await asyncio.to_thread(self._check, password or "")
        if not ok:
            self._fail(ip)
            await asyncio.sleep(0.5 if not os.environ.get("PYTEST_CURRENT_TEST") else 0)
            self.db.event("warn", "auth", "failed dashboard login", ip=ip)
            raise AuthError(401, "bad_password", "Wrong password.")
        self._fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        sessions = self._sessions()
        sessions[hashlib.sha256(token.encode()).hexdigest()] = {"exp": self.clock() + SESSION_SECONDS, "ip": ip}
        self.db.kv_set("admin_sessions", sessions)
        self.db.event("info", "auth", "dashboard login", ip=ip)
        return token

    def _sessions(self) -> dict:
        now = self.clock()
        return {k: v for k, v in (self.db.kv_get("admin_sessions", {}) or {}).items() if v.get("exp", 0) > now}

    def session_valid(self, token: Optional[str]) -> bool:
        if not token:
            return False
        return hashlib.sha256(token.encode()).hexdigest() in self._sessions()

    def logout(self, token: Optional[str]) -> None:
        if not token:
            return
        sessions = self._sessions()
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        self.db.kv_set("admin_sessions", sessions)

    async def change(self, current: str, new: str) -> None:
        if not await asyncio.to_thread(self._check, current or ""):
            raise AuthError(400, "bad_password", "The current password is wrong.")
        if len(new or "") < MIN_LEN:
            raise AuthError(400, "weak_password", f"The new password needs at least {MIN_LEN} characters.")
        if new == current:
            raise AuthError(400, "same_password", "Choose a password different from the temporary one.")
        await asyncio.to_thread(self.set_password, new, False)
