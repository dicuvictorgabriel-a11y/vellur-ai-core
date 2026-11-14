"""Coding-agent tasks: "implement this in repo X, run the tests, prepare the changes for review".

The controller keeps every credential. For a task it downloads a snapshot of the repository from GitHub (token stays
here), hands it to the isolated runner container together with a short-lived inference token, watches time and cost,
stores the log and the diff, and — only when the owner clicks — publishes the changes as a new branch + draft pull
request through the GitHub API. The agent container never sees the GitHub token and has no access to production.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from typing import Optional

import httpx

from .config import ROLES
from .lifecycle import Controller
from .router import Router

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FINAL = ("done", "failed", "cancelled", "timeout", "over_budget")
MAX_TARBALL = 400 * 1024 * 1024


class AgentError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class GitHub:
    """The few GitHub REST calls needed. Endpoints: repos, tarball, git refs/blobs/trees/commits, pulls."""

    def __init__(self, token: str, transport: Optional[httpx.AsyncBaseTransport] = None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "vellur-ai-core"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.c = httpx.AsyncClient(base_url="https://api.github.com", headers=headers, transport=transport,
                                   timeout=60, follow_redirects=True)

    async def _req(self, method: str, path: str, ok=(200, 201), **kw) -> httpx.Response:
        try:
            r = await self.c.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise AgentError("github", f"GitHub unreachable ({e.__class__.__name__})", 502)
        if r.status_code in ok:
            return r
        if r.status_code in (401, 403):
            raise AgentError("github_auth", "GitHub refused the token (missing, expired or without access to this repository)")
        if r.status_code == 404:
            raise AgentError("github_not_found", "repository or branch not found (or the token has no access to it)")
        raise AgentError("github", f"GitHub answered HTTP {r.status_code}: {r.text[:200]}", 502)

    async def default_branch(self, repo: str) -> str:
        return (await self._req("GET", f"/repos/{repo}")).json()["default_branch"]

    async def tarball(self, repo: str, ref: str) -> bytes:
        r = await self._req("GET", f"/repos/{repo}/tarball/{ref}")
        if len(r.content) > MAX_TARBALL:
            raise AgentError("too_large", "repository snapshot is larger than 400 MB")
        return r.content

    async def publish(self, repo: str, base: str, branch: str, files: list, message: str) -> str:
        base_sha = (await self._req("GET", f"/repos/{repo}/git/ref/heads/{base}")).json()["object"]["sha"]
        base_tree = (await self._req("GET", f"/repos/{repo}/git/commits/{base_sha}")).json()["tree"]["sha"]
        entries = []
        for f in files:
            if f.get("deleted"):
                entries.append({"path": f["path"], "mode": "100644", "type": "blob", "sha": None})
                continue
            blob = (await self._req("POST", f"/repos/{repo}/git/blobs",
                                    json={"content": f["content_b64"], "encoding": "base64"})).json()["sha"]
            entries.append({"path": f["path"], "mode": f.get("mode", "100644"), "type": "blob", "sha": blob})
        tree = (await self._req("POST", f"/repos/{repo}/git/trees", json={"base_tree": base_tree, "tree": entries})).json()["sha"]
        commit = (await self._req("POST", f"/repos/{repo}/git/commits",
                                  json={"message": message, "tree": tree, "parents": [base_sha]})).json()["sha"]
        await self._req("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
        return commit

    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> str:
        payload = {"title": title, "head": head, "base": base, "body": body, "draft": True}
        try:
            r = await self._req("POST", f"/repos/{repo}/pulls", json=payload)
        except AgentError as e:
            if "422" not in e.message:
                raise
            payload["draft"] = False     # draft PRs are not available on every plan
            r = await self._req("POST", f"/repos/{repo}/pulls", json=payload)
        return r.json()["html_url"]


class AgentManager:
    def __init__(self, ctrl: Controller, router: Router, runner_transport=None, github_transport=None,
                 poll_seconds: float = 3.0):
        self.ctrl, self.db, self.env, self.router = ctrl, ctrl.db, ctrl.env, router
        self.github_transport, self.poll = github_transport, poll_seconds
        self.runner = httpx.AsyncClient(base_url=self.env.agent_runner_url or "http://agent:7070",
                                        headers={"X-Runner-Secret": self.env.agent_runner_secret},
                                        transport=runner_transport, timeout=httpx.Timeout(30, read=300, write=300))
        self._jobs: dict[int, asyncio.Task] = {}

    # ── helpers ─────────────────────────────────────────────────────────────────────────────────
    def available(self) -> tuple:
        if not self.env.agent_runner_url or len(self.env.agent_runner_secret) < 24:
            return False, "The agent workspace is not installed on the server (sudo vac agent enable)."
        return True, ""

    def github_token(self) -> str:
        return self.db.kv_get("github_token", "") or ""

    def set_github_token(self, token: str) -> None:
        token = (token or "").strip()
        if token and (len(token) < 20 or len(token) > 400 or any(c.isspace() for c in token)):
            raise AgentError("invalid", "that does not look like a GitHub token")
        self.db.kv_set("github_token", token)
        self.db.event("info", "agent", "GitHub token saved" if token else "GitHub token removed")

    def task(self, tid: int) -> Optional[dict]:
        t = self.db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if t:
            t["cost_usd"] = round(self.router.tag_cost(f"task:{tid}"), 4)
        return t

    def list(self, limit: int = 30) -> list:
        rows = self.db.all("SELECT id,created,state,repo,base,branch,model,prompt,error,files_changed,pr_url,started,ended "
                           "FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["cost_usd"] = round(self.router.tag_cost(f"task:{r['id']}"), 4)
        return rows

    def _set(self, tid: int, **fields) -> None:
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.x(f"UPDATE tasks SET {cols} WHERE id=?", tuple(fields.values()) + (tid,))

    async def _runner(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self.runner.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise AgentError("runner", f"agent workspace unreachable ({e.__class__.__name__})", 502)
        if r.status_code == 409:
            raise AgentError("busy", "the agent workspace is busy with another task", 409)
        if r.status_code >= 400:
            raise AgentError("runner", f"agent workspace error {r.status_code}: {r.text[:200]}", 502)
        return r

    # ── API ─────────────────────────────────────────────────────────────────────────────────────
    async def create(self, prompt: str, model: str, repo: str = "", base: str = "") -> dict:
        ok, why = self.available()
        if not ok:
            raise AgentError("unavailable", why, 503)
        prompt, repo, base = (prompt or "").strip(), (repo or "").strip(), (base or "").strip()
        if not 5 <= len(prompt) <= 20000:
            raise AgentError("invalid", "describe the task (5–20000 characters)")
        if model not in ROLES:
            raise AgentError("invalid", "unknown model")
        if repo and not REPO_RE.match(repo):
            raise AgentError("invalid", "repository must look like owner/name")
        if base and not BRANCH_RE.match(base):
            raise AgentError("invalid", "invalid branch name")
        if self.db.one("SELECT id FROM tasks WHERE state NOT IN ('done','failed','cancelled','timeout','over_budget')"):
            raise AgentError("busy", "another task is still running; one at a time on this server", 409)
        if self.ctrl.budget.status(self.ctrl.settings.get(), None, self.ctrl.clock())["exhausted"]:
            raise AgentError("budget", "monthly budget is used up")
        now = time.time()
        cur = self.db.x("INSERT INTO tasks(created,updated,state,repo,base,model,prompt) VALUES(?,?,?,?,?,?,?)",
                        (now, now, "queued", repo, base, model, prompt))
        tid = cur.lastrowid
        self.db.event("info", "agent", f"task {tid} created", repo=repo, model=model)
        self._jobs[tid] = asyncio.create_task(self._run(tid))
        return self.task(tid)

    async def _run(self, tid: int) -> None:
        t = self.task(tid)
        st = self.ctrl.settings.get()
        tag = f"task:{tid}"
        token = self.router.issue_token(tag)
        try:
            self._set(tid, state="preparing")
            source = None
            base = t["base"]
            if t["repo"]:
                gh = GitHub(self.github_token(), self.github_transport)
                base = base or await gh.default_branch(t["repo"])
                source = await gh.tarball(t["repo"], base)
                self._set(tid, base=base)
            prompt = t["prompt"]
            if st["memory"].strip():
                prompt = f"Project notes from the owner:\n{st['memory'].strip()}\n\nTask:\n{prompt}"
            await self._runner("POST", "/tasks", json={"id": tid, "prompt": prompt, "model": t["model"],
                                                       "proxy_url": self.env.agent_proxy_url, "proxy_token": token,
                                                       "max_minutes": st["agent_max_minutes"]})
            if source is not None:
                await self._runner("PUT", f"/tasks/{tid}/source", content=source)
            await self._runner("POST", f"/tasks/{tid}/start")
            started = time.time()
            self._set(tid, state="running", started=started)
            final, info = None, {}
            while final is None:
                await asyncio.sleep(self.poll)
                info = (await self._runner("GET", f"/tasks/{tid}")).json()
                self.db.kv_set(f"task_log_{tid}", ANSI_RE.sub("", info.get("log", ""))[-40000:])
                if info["state"] in ("done", "failed", "cancelled", "timeout"):
                    final = info["state"]
                    break
                state = self.task(tid)["state"]
                if state == "cancelling":
                    await self._runner("POST", f"/tasks/{tid}/cancel")
                elif time.time() - started > 60 * float(st["agent_max_minutes"]):
                    await self._runner("POST", f"/tasks/{tid}/cancel")
                    self._set(tid, state="cancelling", error=f"time limit of {st['agent_max_minutes']} min reached")
                elif float(st["agent_max_usd"]) > 0 and self.router.tag_cost(tag) >= float(st["agent_max_usd"]):
                    await self._runner("POST", f"/tasks/{tid}/cancel")
                    self._set(tid, state="cancelling", error=f"cost limit of ${st['agent_max_usd']:.2f} per task reached")
            prior = self.task(tid)
            if prior["error"].startswith("cost limit"):
                final = "over_budget"
            elif prior["error"].startswith("time limit"):
                final = "timeout"
            with contextlib.suppress(AgentError):
                d = (await self._runner("GET", f"/tasks/{tid}/diff")).json()
                self.db.kv_set(f"task_diff_{tid}", {"stat": d.get("stat", ""), "diff": d.get("diff", "")[:400000]})
            error = prior["error"] or info.get("error", "")
            if final == "failed" and not error:
                error = f"the agent exited with code {info.get('exit')} (see the log)"
            self._set(tid, state=final, files_changed=int(info.get("changed") or 0), ended=time.time(), error=error)
            self.db.event("info" if final == "done" else "warn", "agent", f"task {tid} {final}",
                          files=info.get("changed"), cost=round(self.router.tag_cost(tag), 4))
        except AgentError as e:
            self._set(tid, state="failed", error=e.message, ended=time.time())
            self.db.event("warn", "agent", f"task {tid} failed: {e.message}")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._set(tid, state="failed", error=f"{e.__class__.__name__}: {e}", ended=time.time())
        finally:
            self.router.revoke_tag(tag)
            self._jobs.pop(tid, None)

    async def cancel(self, tid: int) -> dict:
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        if t["state"] not in FINAL:
            self._set(tid, state="cancelling")
        return self.task(tid)

    def detail(self, tid: int) -> dict:
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        d = self.db.kv_get(f"task_diff_{tid}") or {}
        return {**t, "log": self.db.kv_get(f"task_log_{tid}", "") or "", "diff_stat": d.get("stat", ""),
                "diff": d.get("diff", ""), "can_publish": bool(t["state"] == "done" and t["repo"] and t["files_changed"]
                                                               and not t["pr_url"] and self.github_token())}

    async def publish(self, tid: int) -> dict:
        """Owner-initiated: push the result as a new branch and open a draft pull request."""
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        if t["state"] != "done" or not t["repo"] or not t["files_changed"]:
            raise AgentError("invalid", "only a finished task with changes in a GitHub repository can be published")
        if t["pr_url"]:
            return t
        if not self.github_token():
            raise AgentError("github_auth", "save a GitHub token first (Agent → GitHub access)")
        files = (await self._runner("GET", f"/tasks/{tid}/files")).json()["files"]
        gh = GitHub(self.github_token(), self.github_transport)
        branch = f"vellur/task-{tid}"
        title = "VELLUR agent: " + " ".join(t["prompt"].split())[:70]
        await gh.publish(t["repo"], t["base"], branch, files, title + f"\n\nTask {tid}, model {t['model']}.")
        url = await gh.open_pr(t["repo"], branch, t["base"], title,
                               f"Prepared by the VELLUR AI Core agent (task {tid}, model `{t['model']}`).\n\n"
                               f"**Request**\n\n{t['prompt'][:3000]}\n\nReview before merging.")
        self._set(tid, branch=branch, pr_url=url)
        self.db.event("info", "agent", f"task {tid} published", url=url)
        return self.task(tid)

    async def recover(self) -> None:
        """After a controller restart, tasks that were in flight cannot be resumed: mark them and stop the runner."""
        for r in self.db.all("SELECT id FROM tasks WHERE state NOT IN ('done','failed','cancelled','timeout','over_budget')"):
            self._set(r["id"], state="failed", error="the controller restarted while this task was running", ended=time.time())
            with contextlib.suppress(Exception):
                await self._runner("POST", f"/tasks/{r['id']}/cancel")

    async def close(self) -> None:
        for j in list(self._jobs.values()):
            j.cancel()
            with contextlib.suppress(BaseException):
                await j
