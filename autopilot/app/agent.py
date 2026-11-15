"""Coding-agent tasks: "implement this in repo X, run the tests, prepare the changes for review".

The controller keeps every credential. For a task it downloads a snapshot of the repository from GitHub (token stays
here), hands it to the isolated runner container together with a short-lived inference token, watches time and cost,
stores the log and the diff, and — only when the owner clicks — publishes the changes as a new branch + draft pull
request through the GitHub API. The agent container never sees the GitHub token and has no access to production.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
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
OUTPUT_LIMIT = 16384          # max tokens per model reply, set in the runner's OpenCode config
PREVIEW_TTL = 12 * 3600       # lifetime of a preview link token
AUTO_CONTINUE = 2             # automatic "continue" runs when a reply was cut off at OUTPUT_LIMIT
STATUS_RE = re.compile(r"STATUS:\s*\**\s*(COMPLETE|PARTIAL|BLOCKED)\b", re.I)
TEST_RE = re.compile(r"\b(pytest|unittest|jest|vitest|mocha|playwright|cypress|tsc|eslint|ruff|mypy|flake8|go test|cargo test|"
                     r"npm (run )?(test|check|lint|build|typecheck)|pnpm (run )?(test|check|lint|build)|yarn (test|check|lint|build)|"
                     r"make (test|check)|(ba)?sh \S*tests?\.sh|\S*run[_-]?tests?\S*|node --check|python3? -m (pytest|unittest|py_compile))\b")
SHOT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\.png$")

AGENT_GUIDE = """You are an autonomous AI developer working for the owner of this repository through VELLUR AI Core.
You run unattended in an isolated copy of the repository: nobody can answer questions during the run, so make
reasonable decisions and mention them in your report.

How to work:
- Explore the relevant files first, then write a short plan with the todo tool and keep it updated.
- Before each group of actions write ONE short plain sentence saying what you are about to do; the owner reads these
  as live progress updates.
- Work in small steps. Never write a very large file in a single tool call (one reply is limited to about 16 000
  tokens): create big files in several parts or edit existing files piece by piece.
- After changing code, run lightweight code checks and functional tests, type checks, linters or build as appropriate,
  and fix what fails. Basic static website checks do not require Chromium. Do not install browser dependencies unless
  an explicitly requested browser-based test requires them.
- Do not take screenshots or inspect images automatically. If the owner explicitly requests screenshots or visual
  inspection, use `vellur-screenshot <url-or-html-file> <name> [desktop|mobile]` when available. Report an unavailable
  optional screenshot capability separately from a failed test. Never describe screenshots you did not take.
- The owner watches a LIVE PREVIEW of the project next to this conversation. A static site (an index.html in the
  project root or in dist/, build/, public/) is shown automatically. If the project needs a server, create
  `.vellur/preview.json` like {"command": "npm run dev -- --port $PORT --host 127.0.0.1", "dir": "."}; the command must
  listen on $PORT and VELLUR starts it for you. You can open the same preview at $VELLUR_PREVIEW_URL (curl or
  vellur-screenshot). The preview is independent of optional screenshots and visual inspection.
- Do not commit, push or deploy. The owner reviews your changes and publishes them from VELLUR.

Always finish with this report (in the owner's language when their notes ask for one):
## Summary
(two or three sentences)
## Done
(bullet list of what you actually changed)
## Not done
(what is still missing, or "nothing")
## Tests
(commands you ran and their results, or why none)
STATUS: COMPLETE | PARTIAL | BLOCKED
(write STATUS: COMPLETE only if everything requested is done and verified)"""
CONTINUE_PROMPT = ("Your previous reply was cut off because it reached the output limit. Continue the task from where you "
                   "stopped. Write large files in several smaller parts or edit them piece by piece.")
FOLLOWUP_SUFFIX = ("\n\n(When you are done, finish again with the report: ## Summary, ## Done, ## Not done, ## Tests and the "
                   "STATUS line.)")
VISUAL_PROMPT = ("\n\nThe owner explicitly requested AI visual inspection for this run. Use vellur-screenshot on the "
                 "live preview, open the resulting image with the read tool, inspect it and report your observations. "
                 "If screenshots or image inspection are unavailable, report that as unverified, not as a passed test.")


def _loads(v, default):
    try:
        return json.loads(v) if isinstance(v, str) and v else (v if v is not None else default)
    except ValueError:
        return default


def _clip(v, n: int = 4000):
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f"\n… [{len(v) - n} more characters]"
    if isinstance(v, dict):
        return {k: _clip(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [_clip(x, n) for x in v[:100]]
    return v


def normalize(ev: dict):
    """OpenCode `run --format json` event -> (kind, data) for the conversation timeline, or (None, None) to skip."""
    typ, part = ev.get("type"), ev.get("part") or {}
    if typ == "text":
        text = part.get("text") or ""
        return ("text", {"text": text[:30000]}) if text.strip() else (None, None)
    if typ == "reasoning":
        text = part.get("text") or ""
        return ("thinking", {"text": text[:6000]}) if text.strip() else (None, None)
    if typ == "tool_use":
        state = part.get("state") or {}
        meta = state.get("metadata") or {}
        inp = state.get("input") or {}
        out = state.get("output") if isinstance(state.get("output"), str) else ""
        data = {"tool": part.get("tool", "?"), "status": state.get("status", ""), "title": (state.get("title") or "")[:300],
                "input": _clip(inp, 3000), "output": _clip(out or state.get("error") or "", 4000),
                "exit": meta.get("exit") if isinstance(meta.get("exit"), int) else None}
        if part.get("tool") == "todowrite" and isinstance(inp.get("todos"), list):
            data["todos"] = [{"content": str(x.get("content", ""))[:200], "status": x.get("status", "")}
                             for x in inp["todos"][:30] if isinstance(x, dict)]
        return "tool", data
    if typ == "step_finish":
        return "step", {"reason": part.get("reason", ""), "tokens": part.get("tokens") or {}}
    if typ == "error":
        err = ev.get("error") or {}
        msg = ((err.get("data") or {}).get("message") or err.get("name") or "error") if isinstance(err, dict) else str(err)
        return "error", {"message": str(msg)[:2000]}
    if typ == "raw":
        return ("notice" if ev.get("category") == "screenshot_notice" else "raw"), {"text": str(ev.get("text", ""))[:2000]}
    return None, None


def sections(text: str) -> dict:
    """Split the agent's final report into its ## sections (summary, done, not_done, tests)."""
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"^\s*#{1,4}\s*(summary|done|not done|tests?)\s*:?\s*$", line, re.I)
        if m:
            key = m.group(1).lower()
            cur = "tests" if key.startswith("test") else key.replace(" ", "_")
            out[cur] = ""
        elif cur and not STATUS_RE.search(line):
            out[cur] += line + "\n"
    return {k: v.strip()[:6000] for k, v in out.items()}


def pr_body(t: dict, rep: dict) -> str:
    files = "\n".join(f"- `{f['path']}`" for f in (rep.get("files") or [])[:60]) or "- (see the diff)"
    tests = "\n".join(f"- `{c['command'][:120]}` → {'passed' if c.get('exit') == 0 else 'failed'}"
                      for c in (rep.get("tests") or [])[:20]) or "- none recorded"
    return (f"Prepared by the VELLUR AI Core agent (task {t['id']}, model `{t['model']}`). **Review before merging.**\n\n"
            f"**Request**\n\n{t['prompt'][:3000]}\n\n**Agent report** (outcome: {rep.get('outcome', 'unknown')})\n\n"
            f"{(rep.get('final_text') or '(no report)')[:6000]}\n\n**Files**\n\n{files}\n\n**Checks run by the agent**\n\n{tests}\n")


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

    async def repos(self) -> list:
        """Repositories the token can see, most recently pushed first (at most 300)."""
        out = []
        for page in (1, 2, 3):
            r = await self._req("GET", "/user/repos", params={"per_page": 100, "page": page, "sort": "pushed",
                                                             "affiliation": "owner,collaborator,organization_member"})
            batch = r.json()
            out += [{"name": x["full_name"], "default_branch": x.get("default_branch") or "main",
                     "private": bool(x.get("private")), "pushed_at": x.get("pushed_at") or ""} for x in batch]
            if len(batch) < 100:
                break
        return out

    async def branches(self, repo: str) -> list:
        r = await self._req("GET", f"/repos/{repo}/branches", params={"per_page": 100})
        return [b["name"] for b in r.json()]

    async def default_branch(self, repo: str) -> str:
        return (await self._req("GET", f"/repos/{repo}")).json()["default_branch"]

    async def tarball(self, repo: str, ref: str) -> bytes:
        r = await self._req("GET", f"/repos/{repo}/tarball/{ref}")
        if len(r.content) > MAX_TARBALL:
            raise AgentError("too_large", "repository snapshot is larger than 400 MB")
        return r.content

    async def head_sha(self, repo: str, branch: str) -> str:
        return (await self._req("GET", f"/repos/{repo}/git/ref/heads/{branch}")).json()["object"]["sha"]

    async def _commit(self, repo: str, base_sha: str, files: list, message: str) -> str:
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
        return (await self._req("POST", f"/repos/{repo}/git/commits",
                                json={"message": message, "tree": tree, "parents": [base_sha]})).json()["sha"]

    async def publish(self, repo: str, base: str, branch: str, files: list, message: str, update: bool = False) -> str:
        commit = await self._commit(repo, await self.head_sha(repo, base), files, message)
        if update:   # our own task branch, already open as a pull request: move it to the new commit
            await self._req("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", json={"sha": commit, "force": True})
        else:
            await self._req("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
        return commit

    async def publish_direct(self, repo: str, base: str, expected_sha: str, files: list, message: str) -> str:
        """Commit straight onto the base branch — only as a fast-forward from the commit the workspace started from."""
        head = await self.head_sha(repo, base)
        if expected_sha and head != expected_sha:
            raise AgentError("moved", f"{base} changed on GitHub since this workspace started, so publishing directly could "
                                      "overwrite newer work. Create a pull request instead.", 409)
        commit = await self._commit(repo, head, files, message)
        await self._req("PATCH", f"/repos/{repo}/git/refs/heads/{base}", json={"sha": commit, "force": False})
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
        self.activity: dict[int, list] = {}      # processes running right now in each task (from the runner)
        self.previews: dict[str, tuple] = {}     # preview link token -> (task id, expiry); minted only for a logged-in owner

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

    async def repos(self) -> list:
        if not self.github_token():
            return []
        gh = GitHub(self.github_token(), self.github_transport)
        try:
            return await gh.repos()
        finally:
            await gh.c.aclose()

    async def branches(self, repo: str) -> dict:
        repo = (repo or "").strip()
        if not REPO_RE.match(repo):
            raise AgentError("invalid", "repository must look like owner/name")
        gh = GitHub(self.github_token(), self.github_transport)
        try:
            default = await gh.default_branch(repo)
            names = await gh.branches(repo)
        finally:
            await gh.c.aclose()
        names = [default] + sorted(n for n in names if n != default)
        return {"default": default, "branches": names}

    def task(self, tid: int) -> Optional[dict]:
        t = self.db.one("SELECT * FROM tasks WHERE id=?", (tid,))
        if t:
            t["cost_usd"] = round(self.router.tag_cost(f"task:{tid}"), 4)
            t["report"] = _loads(t.get("report"), {})
            t["activity"] = self.activity.get(tid, [])
        return t

    def list(self, limit: int = 40) -> list:
        rows = self.db.all("SELECT id,created,updated,state,outcome,runs,repo,base,branch,model,prompt,error,files_changed,"
                           "pr_url,started,ended FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["cost_usd"] = round(self.router.tag_cost(f"task:{r['id']}"), 4)
            r["title"] = " ".join(r["prompt"].split())[:80]
        return rows

    def _set(self, tid: int, **fields) -> None:
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.x(f"UPDATE tasks SET {cols} WHERE id=?", tuple(fields.values()) + (tid,))

    def _ev(self, _tid: int, _run: int, _kind: str, **data) -> None:
        self.db.x("INSERT INTO task_events(task_id,ts,run,kind,data) VALUES(?,?,?,?,?)",
                  (_tid, time.time(), _run, _kind, json.dumps(data)))
        tid = _tid
        self.db.x("UPDATE tasks SET updated=? WHERE id=?", (time.time(), tid))

    def events(self, tid: int, after: int = 0, limit: int = 500) -> list:
        rows = self.db.all("SELECT id,ts,run,kind,data FROM task_events WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
                           (tid, after, limit))
        for r in rows:
            r["data"] = _loads(r["data"], {})
        return rows

    async def _runner(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = await self.runner.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise AgentError("runner", f"agent workspace unreachable ({e.__class__.__name__})", 502)
        if r.status_code == 409:
            raise AgentError("busy", "the agent workspace is busy with another task", 409)
        if r.status_code in (404, 410):
            raise AgentError("expired", "the workspace of this task no longer exists on the server "
                                        "(only the latest tasks are kept); start a new task", 410)
        if r.status_code >= 400:
            raise AgentError("runner", f"agent workspace error {r.status_code}: {r.text[:200]}", 502)
        return r

    def _check_can_run(self) -> None:
        ok, why = self.available()
        if not ok:
            raise AgentError("unavailable", why, 503)
        if self.db.one("SELECT id FROM tasks WHERE state NOT IN ('done','failed','cancelled','timeout','over_budget')"):
            raise AgentError("busy", "another task is still running; one at a time on this server", 409)
        if self.ctrl.budget.status(self.ctrl.settings.get(), None, self.ctrl.clock())["exhausted"]:
            raise AgentError("budget", "monthly budget is used up")

    # ── API ─────────────────────────────────────────────────────────────────────────────────────
    async def create(self, prompt: str, model: str, repo: str = "", base: str = "", visual_inspection: bool = False,
                     auto_screenshots: bool = False) -> dict:
        prompt, repo, base = (prompt or "").strip(), (repo or "").strip(), (base or "").strip()
        if not 5 <= len(prompt) <= 20000:
            raise AgentError("invalid", "describe the task (5–20000 characters)")
        if model not in ROLES:
            raise AgentError("invalid", "unknown model")
        if repo and not REPO_RE.match(repo):
            raise AgentError("invalid", "repository must look like owner/name")
        if base and not BRANCH_RE.match(base):
            raise AgentError("invalid", "invalid branch name")
        if visual_inspection and not self.ctrl.settings.get().get("agent_vision"):
            raise AgentError("vision_unavailable", "Enable image support for the selected model and its fallbacks before requesting AI visual inspection")
        self._check_can_run()
        now = time.time()
        cur = self.db.x("INSERT INTO tasks(created,updated,state,repo,base,model,prompt,outcome,runs) VALUES(?,?,?,?,?,?,?,?,?)",
                        (now, now, "queued", repo, base, model, prompt, "", 1))
        tid = cur.lastrowid
        self._ev(tid, 1, "user", text=prompt)
        self.db.event("info", "agent", f"task {tid} created", repo=repo, model=model)
        self._jobs[tid] = asyncio.create_task(self._drive(tid, prompt, first=True, visual_inspection=visual_inspection, auto_screenshots=auto_screenshots))
        return self.task(tid)

    async def message(self, tid: int, text: str, model: Optional[str] = None, visual_inspection: bool = False,
                      auto_screenshots: bool = False) -> dict:
        """Follow-up: continue the same OpenCode session in the same workspace (the agent keeps its context)."""
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        text = (text or "").strip()
        if not 1 <= len(text) <= 20000:
            raise AgentError("invalid", "write a message (up to 20000 characters)")
        if t["state"] not in FINAL:
            raise AgentError("busy", "the agent is still working on this task; wait or stop it first", 409)
        if model and model not in ROLES:
            raise AgentError("invalid", "unknown model")
        if not t["started"]:
            raise AgentError("expired", "this task never started an agent session; start a new task", 410)
        if visual_inspection and not self.ctrl.settings.get().get("agent_vision"):
            raise AgentError("vision_unavailable", "Enable image support for the selected model and its fallbacks before requesting AI visual inspection")
        self._check_can_run()
        run = int(t["runs"] or 1) + 1
        self._set(tid, state="queued", runs=run, error="", outcome="", model=model or t["model"], ended=None)
        self._ev(tid, run, "user", text=text)
        self._jobs[tid] = asyncio.create_task(self._drive(tid, text, first=False, visual_inspection=visual_inspection, auto_screenshots=auto_screenshots))
        return self.task(tid)

    def _first_prompt(self, prompt: str, repo: str = "") -> str:
        st = self.ctrl.settings.get()
        memory = st["memory"].strip()
        notes = f"Notes from the owner (always follow them):\n{memory}\n\n" if memory else ""
        extra = ""
        if repo and repo == self.env.self_repo:
            extra += ("\n- This repository is VELLUR AI Core itself, the application you are running in. You work on a "
                      "copy: the live server is not affected until the owner publishes and installs a release. Read "
                      "AGENTS.md first; never change the frozen login files; run the tests it names before reporting.")
        return f"{AGENT_GUIDE}{extra}\n\n{notes}Task from the owner:\n{prompt}"

    async def _drive(self, tid: int, prompt: str, first: bool, visual_inspection: bool = False,
                     auto_screenshots: bool = False) -> None:
        """One run (first task message or follow-up), plus automatic continuations when the reply was cut off."""
        tag = f"task:{tid}"
        try:
            await self._one_run(tid, prompt, first, tag, visual_inspection, auto_screenshots)
            for _ in range(AUTO_CONTINUE):
                t = self.task(tid)
                if not (t["state"] == "done" and t["report"].get("stop_reason") == "length"):
                    break
                run = int(t["runs"]) + 1
                self._set(tid, state="queued", runs=run, outcome="")
                self._ev(tid, run, "notice", text="The model's reply was cut off at its output limit. "
                                                  "VELLUR asked it automatically to continue in smaller steps.")
                await self._one_run(tid, CONTINUE_PROMPT, False, tag, visual_inspection)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            run = int((self.task(tid) or {}).get("runs") or 1)
            self._set(tid, state="failed", outcome="failed", error=f"{e.__class__.__name__}: {e}", ended=time.time())
            self._ev(tid, run, "error", message=f"{e.__class__.__name__}: {e}")
        finally:
            self._jobs.pop(tid, None)

    async def _one_run(self, tid: int, prompt: str, first: bool, tag: str, visual_inspection: bool = False,
                       auto_screenshots: bool = False) -> None:
        t = self.task(tid)
        st = self.ctrl.settings.get()
        run = int(t["runs"] or 1)
        token = self.router.issue_token(tag)
        cost0 = self.router.tag_cost(tag)
        info: dict = {}
        try:
            self._set(tid, state="preparing")
            if first:
                source, base = None, t["base"]
                if t["repo"]:
                    self._ev(tid, run, "status", text=f"Downloading {t['repo']} from GitHub")
                    gh = GitHub(self.github_token(), self.github_transport)
                    try:
                        base = base or await gh.default_branch(t["repo"])
                        sha = ""
                        with contextlib.suppress(AgentError, KeyError):
                            sha = await gh.head_sha(t["repo"], base)
                        source = await gh.tarball(t["repo"], sha or base)
                    finally:
                        await gh.c.aclose()
                    self._set(tid, base=base, base_sha=sha)
                await self._runner("POST", "/tasks", json={"id": tid, "prompt": self._first_prompt(prompt, t["repo"]) + (VISUAL_PROMPT if visual_inspection else ""),
                                                           "model": t["model"], "proxy_url": self.env.agent_proxy_url,
                                                           "proxy_token": token, "max_minutes": st["agent_max_minutes"],
                                                           "vision": visual_inspection, "auto_screenshots": auto_screenshots})
                if source is not None:
                    await self._runner("PUT", f"/tasks/{tid}/source", content=source)
                await self._runner("POST", f"/tasks/{tid}/start")
            else:
                await self._runner("POST", f"/tasks/{tid}/message", json={
                    "prompt": prompt + FOLLOWUP_SUFFIX + (VISUAL_PROMPT if visual_inspection else ""),
                    "model": self.task(tid)["model"], "proxy_url": self.env.agent_proxy_url,
                    "proxy_token": token, "max_minutes": st["agent_max_minutes"], "vision": visual_inspection,
                    "auto_screenshots": auto_screenshots})
            started = time.time()
            self._set(tid, state="running", started=t["started"] or started)
            self._ev(tid, run, "status", text="Agent started" if first else "Agent continues", state="running")
            limit_note = ""
            while True:
                await asyncio.sleep(self.poll)
                info = (await self._runner("GET", f"/tasks/{tid}")).json()
                self.activity[tid] = info.get("activity") or []
                self.db.kv_set(f"task_log_{tid}", ANSI_RE.sub("", info.get("log", ""))[-40000:])
                await self._import_events(tid)
                await self._import_shots(tid, run)
                if info["state"] in ("done", "failed", "cancelled", "timeout"):
                    break
                state = self.task(tid)["state"]
                if state == "cancelling":
                    await self._runner("POST", f"/tasks/{tid}/cancel")
                elif time.time() - started > 60 * float(st["agent_max_minutes"]):
                    await self._runner("POST", f"/tasks/{tid}/cancel")
                    limit_note = f"time limit of {st['agent_max_minutes']} min reached"
                    self._set(tid, state="cancelling", error=limit_note)
                elif float(st["agent_max_usd"]) > 0 and self.router.tag_cost(tag) - cost0 >= float(st["agent_max_usd"]):
                    await self._runner("POST", f"/tasks/{tid}/cancel")
                    limit_note = f"cost limit of ${st['agent_max_usd']:.2f} per run reached"
                    self._set(tid, state="cancelling", error=limit_note)
            self.activity.pop(tid, None)
            await self._import_events(tid)
            await self._import_shots(tid, run)
            final = info["state"]
            if limit_note.startswith("cost limit"):
                final = "over_budget"
            elif limit_note.startswith("time limit"):
                final = "timeout"
            files = []
            with contextlib.suppress(AgentError):
                files = (await self._runner("GET", f"/tasks/{tid}/summary")).json().get("files", [])
            with contextlib.suppress(AgentError):
                d = (await self._runner("GET", f"/tasks/{tid}/diff")).json()
                self.db.kv_set(f"task_diff_{tid}", {"stat": d.get("stat", ""), "diff": d.get("diff", "")[:400000]})
            error = limit_note or info.get("error", "")
            if final == "failed" and not error:
                error = f"the agent process exited with code {info.get('exit')}"
            report = self._report(tid, run, final, error, files, self.router.tag_cost(tag) - cost0)
            self._set(tid, state=final, outcome=report["outcome"], report=json.dumps(report),
                      files_changed=len(files) or int(info.get("changed") or 0), ended=time.time(), error=error)
            self._ev(tid, run, "report", **report)
            self.db.event("info" if report["outcome"] == "completed" else "warn", "agent",
                          f"task {tid} run {run}: {report['outcome']}", files=len(files),
                          cost=round(self.router.tag_cost(tag), 4))
        except AgentError as e:
            self._set(tid, state="failed", outcome="failed", error=e.message, ended=time.time())
            self._ev(tid, run, "error", message=e.message)
        finally:
            self.router.revoke_tag(tag)

    async def _import_events(self, tid: int) -> None:
        cursor = int(self.task(tid)["cursor"] or 0)
        while True:
            r = (await self._runner("GET", f"/tasks/{tid}/events", params={"after": cursor})).json()
            evs = r.get("events", [])
            for ev in evs:
                kind, data = normalize(ev)
                if kind:
                    self._ev(tid, int(ev.get("run") or 1), kind, **data)
            cursor += len(evs)
            self._set(tid, cursor=cursor)
            if len(evs) < 400:
                return

    async def _import_shots(self, tid: int, run: int) -> None:
        try:
            names = (await self._runner("GET", f"/tasks/{tid}/shots")).json().get("shots", [])
        except AgentError:
            return
        have = {e["data"].get("name") for e in self.events(tid, 0, 100000) if e["kind"] == "shot"}
        folder = os.path.join(self.env.data_dir, "shots", f"t{tid}")
        for n in names:
            if n in have or not SHOT_RE.match(n):
                continue
            with contextlib.suppress(AgentError):
                png = (await self._runner("GET", f"/tasks/{tid}/shots/{n}")).content
                if not png.startswith(b"\x89PNG"):
                    continue
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, n), "wb") as f:
                    f.write(png)
                self._ev(tid, run, "shot", name=n)

    def shot_path(self, tid: int, name: str) -> Optional[str]:
        if not SHOT_RE.match(name or ""):
            return None
        p = os.path.join(self.env.data_dir, "shots", f"t{int(tid)}", name)
        return p if os.path.isfile(p) else None

    def _report(self, tid: int, run: int, final: str, error: str, files: list, run_cost: float) -> dict:
        """What the owner reads at the end of a run. Built only from recorded facts; the outcome is judged, not assumed."""
        evs = [e for e in self.events(tid, 0, 100000) if e["run"] == run or e["kind"] in ("user", "shot")]
        run_evs = [e for e in evs if e["run"] == run]
        texts = [e["data"]["text"] for e in run_evs if e["kind"] == "text" and e["data"].get("text", "").strip()]
        final_text = texts[-1] if texts else ""
        m = STATUS_RE.search(final_text)
        claim = m.group(1).upper() if m else ""
        requests = [e["data"]["text"] for e in evs if e["kind"] == "user"]
        steps = [e["data"] for e in run_evs if e["kind"] == "step"]
        stop_reason = steps[-1].get("reason", "") if steps else ""
        commands, last_test = [], {}
        for e in run_evs:
            d = e["data"]
            if e["kind"] == "tool" and d.get("tool") == "bash":
                cmd = (d.get("input") or {}).get("command", "")
                c = {"command": cmd[:300], "exit": d.get("exit"), "status": d.get("status"), "test": bool(TEST_RE.search(cmd))}
                commands.append(c)
                if c["test"]:
                    last_test[cmd] = c
        tests = list(last_test.values())
        tests_failed = [c for c in tests if c["status"] == "error" or (c["exit"] not in (None, 0))]
        errors = [e["data"].get("message", "") for e in run_evs if e["kind"] == "error"]
        if error and not (errors and error.startswith("the agent process exited")):
            errors.append(error)          # the generic exit-code line adds nothing when the real error is known
        credit = next((x for x in errors if "provider_credit" in x or "no credit left" in x), "")
        shots = [e["data"]["name"] for e in evs if e["kind"] == "shot"]
        tokens_out = sum(int((s.get("tokens") or {}).get("output") or 0) for s in steps)
        tokens_in = sum(int((s.get("tokens") or {}).get("input") or 0) for s in steps)
        if final in ("timeout", "over_budget"):
            outcome, why = "stopped", f"Stopped by the safety limit: {error}."
        elif final == "cancelled":
            outcome, why = "cancelled", "You stopped this run."
        elif credit:
            outcome, why = "blocked", ("The model provider has no credit left for this model, so the agent could not work"
                                       + (" (nothing was changed)." if not files else ".") +
                                       " Choose another model below and reply “continue”, add credit at the provider, "
                                       "or add a backup model to this route in Models.")
        elif final == "failed":
            outcome, why = "failed", "The agent process failed: " + (errors[0] if errors else "unknown error")
        elif stop_reason == "length":
            outcome, why = "incomplete", ("The model's reply was cut off at its output limit, so the agent stopped in the "
                                          "middle of the work. Send a follow-up such as “continue” to resume.")
        elif errors:
            outcome, why = "failed", "The model provider returned an error: " + errors[-1]
        elif tests_failed:
            outcome, why = "needs_attention", f"{len(tests_failed)} of {len(tests)} checks failed in their last run."
        elif claim == "COMPLETE":
            outcome, why = "completed", ("The agent reports the task as complete" +
                                         (" and the checks it ran passed." if tests else "; it ran no tests."))
        elif claim == "PARTIAL":
            outcome, why = "partial", "The agent reports that part of the task is still open (see “Not done”)."
        elif claim == "BLOCKED":
            outcome, why = "blocked", "The agent reports it could not continue (see its explanation)."
        else:
            outcome, why = "unverified", ("The agent stopped without its final report, so it is not confirmed that the "
                                          "task is finished. Ask it to continue or to report what is left.")
        return {"run": run, "requested": requests[-1] if requests else "", "final_text": final_text.strip()[:20000],
                "claim": claim, "sections": sections(final_text), "outcome": outcome, "why": why,
                "process": final, "stop_reason": stop_reason, "files": files, "commands": commands[-60:], "tests": tests[-30:],
                "shots": shots, "errors": errors[-10:], "provider_credit": bool(credit), "cost_run_usd": round(run_cost, 4),
                "cost_total_usd": round(self.router.tag_cost(f"task:{tid}"), 4), "tokens": {"input": tokens_in, "output": tokens_out},
                "tool_calls": sum(1 for e in run_evs if e["kind"] == "tool")}

    def diagnostics(self, tid: int) -> dict:
        """Facts from the model router about this task's requests (works for tasks from before the event log too)."""
        rows = self.db.all("SELECT ok,status,in_tokens,out_tokens,cost_usd,model,provider,error FROM usage WHERE tag=? ORDER BY id",
                           (f"task:{tid}",))
        outs = [int(r["out_tokens"] or 0) for r in rows]
        return {"requests": len(rows), "failed_requests": sum(1 for r in rows if not r["ok"]),
                "input_tokens": sum(int(r["in_tokens"] or 0) for r in rows), "output_tokens": sum(outs),
                "largest_reply_tokens": max(outs) if outs else 0, "last_reply_tokens": outs[-1] if outs else 0,
                "models": sorted({f"{r['provider']}:{r['model']}" for r in rows}),
                "errors": [r["error"] for r in rows if r["error"]][-5:], "output_limit_tokens": OUTPUT_LIMIT}

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
        has_events = bool(self.db.one("SELECT 1 AS x FROM task_events WHERE task_id=? LIMIT 1", (tid,)))
        return {**t, "legacy": not has_events, "log": self.db.kv_get(f"task_log_{tid}", "") or "",
                "diff_stat": d.get("stat", ""), "diagnostics": self.diagnostics(tid),
                "can_publish": bool(t["state"] in FINAL and t["repo"] and t["files_changed"] and self.github_token()),
                "can_message": bool(t["state"] in FINAL and t["started"] and has_events)}

    def diff(self, tid: int) -> dict:
        if not self.task(tid):
            raise AgentError("not_found", "unknown task", 404)
        d = self.db.kv_get(f"task_diff_{tid}") or {}
        return {"stat": d.get("stat", ""), "diff": d.get("diff", "")}

    async def publish(self, tid: int) -> dict:
        """Owner-initiated: push the result as a branch and open (or update) a draft pull request."""
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        if t["state"] not in FINAL or not t["repo"] or not t["files_changed"]:
            raise AgentError("invalid", "only a finished task with changes in a GitHub repository can be published")
        if not self.github_token():
            raise AgentError("github_auth", "save a GitHub token first (Agent → GitHub connection)")
        files = (await self._runner("GET", f"/tasks/{tid}/files")).json()["files"]
        gh = GitHub(self.github_token(), self.github_transport)
        branch = t["branch"] or f"vellur/task-{tid}"
        title = "VELLUR agent: " + " ".join(t["prompt"].split())[:70]
        rep = t["report"]
        try:
            await gh.publish(t["repo"], t["base"], branch, files, title + f"\n\nTask {tid}, model {t['model']}.",
                             update=bool(t["pr_url"]))
            url = t["pr_url"] or await gh.open_pr(t["repo"], branch, t["base"], title, pr_body(t, rep))
        finally:
            await gh.c.aclose()
        self._set(tid, branch=branch, pr_url=url)
        self._ev(tid, int(t["runs"] or 1), "pr", url=url, updated=bool(t["pr_url"]))
        self.db.event("info", "agent", f"task {tid} published", url=url)
        return self.task(tid)

    # ── live preview ────────────────────────────────────────────────────────────────────────────
    def preview_link(self, tid: int) -> str:
        """A 12-hour link token for this task's preview. Only a logged-in owner can mint one (admin endpoint)."""
        if not self.task(tid):
            raise AgentError("not_found", "unknown task", 404)
        now = time.time()
        self.previews = {k: v for k, v in self.previews.items() if v[1] > now}
        tok = secrets.token_urlsafe(24)
        self.previews[tok] = (tid, now + PREVIEW_TTL)
        return tok

    def preview_task(self, token: str) -> Optional[int]:
        tid, exp = self.previews.get(token or "", (None, 0))
        return tid if tid and exp > time.time() else None

    async def preview_info(self, tid: int) -> dict:
        if not self.task(tid):
            raise AgentError("not_found", "unknown task", 404)
        try:
            return (await self._runner("GET", f"/tasks/{tid}/preview-info")).json()
        except AgentError as e:
            return {"mode": "none", "why": e.message, "running": False}

    async def preview_fetch(self, tid: int, method: str, path: str, query: str, headers: dict, body: bytes) -> httpx.Response:
        url = f"/tasks/{tid}/preview/{path}" + (f"?{query}" if query else "")
        try:
            return await self.runner.request(method, url, content=body or None, headers=headers)
        except httpx.HTTPError as e:
            raise AgentError("runner", f"agent workspace unreachable ({e.__class__.__name__})", 502)

    # ── checkpoints ─────────────────────────────────────────────────────────────────────────────
    async def checkpoints(self, tid: int) -> list:
        if not self.task(tid):
            raise AgentError("not_found", "unknown task", 404)
        return (await self._runner("GET", f"/tasks/{tid}/checkpoints")).json()["checkpoints"]

    async def _after_files_changed(self, tid: int, changed: int) -> None:
        with contextlib.suppress(AgentError):
            d = (await self._runner("GET", f"/tasks/{tid}/diff")).json()
            self.db.kv_set(f"task_diff_{tid}", {"stat": d.get("stat", ""), "diff": d.get("diff", "")[:400000]})
        self._set(tid, files_changed=int(changed))

    async def save_checkpoint(self, tid: int, label: str) -> dict:
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        if t["state"] not in FINAL:
            raise AgentError("busy", "the agent is working; a checkpoint is saved automatically when it finishes", 409)
        label = " ".join((label or "").split())[:100] or "Checkpoint"
        r = (await self._runner("POST", f"/tasks/{tid}/checkpoints", json={"label": label})).json()
        self._ev(tid, int(t["runs"] or 1), "checkpoint", id=r["id"], label=label)
        return {"id": r["id"], "checkpoints": r["checkpoints"]}

    async def restore_checkpoint(self, tid: int, cid: str) -> dict:
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        if t["state"] not in FINAL:
            raise AgentError("busy", "stop the agent before restoring an earlier version", 409)
        if not re.fullmatch(r"[0-9a-f]{40}", cid or ""):
            raise AgentError("invalid", "unknown checkpoint")
        r = (await self._runner("POST", f"/tasks/{tid}/restore", json={"id": cid})).json()
        label = next((c["label"] for c in r["checkpoints"] if c["id"] == cid), "earlier version")
        await self._after_files_changed(tid, r.get("changed", 0))
        self._ev(tid, int(t["runs"] or 1), "restored", id=cid, label=label, files=r.get("changed", 0))
        return {"id": r["id"], "checkpoints": r["checkpoints"], "task": self.task(tid)}

    async def publish_direct(self, tid: int) -> dict:
        """Owner-initiated: commit the result straight onto the branch the workspace started from (no pull request)."""
        t = self.task(tid)
        if not t:
            raise AgentError("not_found", "unknown task", 404)
        if t["state"] not in FINAL or not t["repo"] or not t["files_changed"]:
            raise AgentError("invalid", "only a finished task with changes in a GitHub repository can be published")
        if not self.github_token():
            raise AgentError("github_auth", "save a GitHub token first (Agent → GitHub connection)")
        files = (await self._runner("GET", f"/tasks/{tid}/files")).json()["files"]
        title = "VELLUR agent: " + " ".join(t["prompt"].split())[:70]
        gh = GitHub(self.github_token(), self.github_transport)
        try:
            commit = await gh.publish_direct(t["repo"], t["base"], t.get("base_sha") or "", files,
                                             title + f"\n\nTask {tid}, model {t['model']}. Published from VELLUR Studio.")
        finally:
            await gh.c.aclose()
        with contextlib.suppress(AgentError):
            await self._runner("POST", f"/tasks/{tid}/rebaseline")
        self.db.kv_set(f"task_diff_{tid}", {"stat": "", "diff": ""})
        self._set(tid, base_sha=commit, files_changed=0)
        url = f"https://github.com/{t['repo']}/commit/{commit}"
        self._ev(tid, int(t["runs"] or 1), "published", url=url, branch=t["base"], files=len(files))
        self.db.event("info", "agent", f"task {tid} published to {t['base']}", url=url)
        return self.task(tid)

    async def recover(self) -> None:
        """After a controller restart, tasks that were in flight cannot be resumed: mark them and stop the runner."""
        for r in self.db.all("SELECT id FROM tasks WHERE state NOT IN ('done','failed','cancelled','timeout','over_budget')"):
            self._set(r["id"], state="failed", outcome="failed", error="the controller restarted while this task was running",
                      ended=time.time())
            self._ev(r["id"], int(self.task(r["id"])["runs"] or 1), "error",
                     message="The server restarted while this task was running. Send a follow-up to continue.")
            with contextlib.suppress(Exception):
                await self._runner("POST", f"/tasks/{r['id']}/cancel")

    async def close(self) -> None:
        for j in list(self._jobs.values()):
            j.cancel()
            with contextlib.suppress(BaseException):
                await j
