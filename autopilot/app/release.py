"""Releases of VELLUR itself: the owner approves an exact commit in the dashboard; a small root script on the server
(bin/vac-autoupdate, outside every container) installs it after the commit's tests passed, and rolls back on failure.

This module only records the owner's decision and shows information. It cannot install anything: the controller has
no access to the host. The agent has no access to these endpoints (they need the owner's login).
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

import httpx

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Release:
    def __init__(self, db, repo: str, token: Callable[[], str], transport: Optional[httpx.AsyncBaseTransport] = None,
                 branch: str = "main"):
        self.db, self.repo, self.branch, self.token, self.transport = db, repo, branch, token, transport

    def state(self) -> dict:
        s = {"approved": "", "approved_at": 0, "request": "", "installed": "", "previous": "", "last": "", "seen": 0}
        s.update(self.db.kv_get("release") or {})
        return s

    def _save(self, **changes) -> dict:
        s = self.state()
        s.update(changes)
        self.db.kv_set("release", s)
        return s

    async def _gh(self, path: str) -> httpx.Response:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vellur-ai-core"}
        if self.token():
            headers["Authorization"] = f"Bearer {self.token()}"
        async with httpx.AsyncClient(base_url="https://api.github.com", headers=headers, transport=self.transport, timeout=20) as c:
            try:
                return await c.get(path)
            except httpx.HTTPError as e:
                raise ReleaseError("github", f"GitHub unreachable ({e.__class__.__name__})", 502)

    async def _ci(self, sha: str) -> str:
        """passed | failed | pending | none | unknown — the same rule the installer applies."""
        r = await self._gh(f"/repos/{self.repo}/commits/{sha}/check-runs?per_page=100")
        if r.status_code != 200:
            return "unknown"
        runs = r.json().get("check_runs", [])
        if not runs:
            return "none"
        if any(x.get("status") != "completed" for x in runs):
            return "pending"
        return "passed" if all(x.get("conclusion") in ("success", "skipped", "neutral") for x in runs) else "failed"

    async def overview(self) -> dict:
        s = self.state()
        out = {**s, "repo": self.repo, "branch": self.branch, "installer_active": time.time() - float(s["seen"] or 0) < 600,
               "latest": None, "error": ""}
        try:
            r = await self._gh(f"/repos/{self.repo}/commits/{self.branch}")
            if r.status_code == 200:
                j = r.json()
                out["latest"] = {"sha": j["sha"], "message": (j["commit"]["message"] or "").split("\n")[0][:140],
                                 "date": j["commit"]["committer"]["date"], "ci": await self._ci(j["sha"])}
            else:
                out["error"] = f"GitHub answered {r.status_code} for {self.repo}"
        except ReleaseError as e:
            out["error"] = e.message
        return out

    async def approve(self, sha: str) -> dict:
        sha = (sha or "").strip()
        if not SHA_RE.match(sha):
            raise ReleaseError("invalid", "a release is identified by a full commit id")
        r = await self._gh(f"/repos/{self.repo}/commits/{sha}")
        if r.status_code != 200 or r.json().get("sha") != sha:
            raise ReleaseError("not_found", f"that commit does not exist in {self.repo}", 404)
        self.db.event("info", "release", f"owner approved release {sha[:7]}")
        return self._save(approved=sha, approved_at=time.time(), request="")

    def cancel(self) -> dict:
        return self._save(approved="", request="")

    def request_rollback(self) -> dict:
        self.db.event("warn", "release", "owner requested a rollback to the previous release")
        return self._save(request="rollback", approved="")

    # ── called by the server-side installer (secret header) ─────────────────────────────────────
    def pending(self) -> dict:
        s = self._save(seen=time.time())
        return {"approved": s["approved"], "approved_at": s["approved_at"], "request": s["request"]}

    def report(self, installed: str, previous: str, last: str, clear: str) -> dict:
        changes = {"installed": installed[:40], "previous": previous[:60], "last": last[:300], "seen": time.time()}
        if clear == "approved":
            changes["approved"] = ""
        if clear == "rollback":
            changes["request"] = ""
        if last and last != self.state()["last"]:
            self.db.event("info", "release", last[:200])
        return self._save(**changes)
