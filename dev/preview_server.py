"""Staging preview of the VELLUR dashboard, used when VELLUR (or anyone) works on VELLUR itself.

It starts the real controller app from THIS checkout with an empty demo database in a temporary folder, no provider
keys, no GPU, no agent runner and dry-run mode, and lets the previewed page act as a signed-in owner so every screen
can be looked at. It never touches the production data or the production process, and install.sh does not copy this
file to the server.

    PORT=4173 python3 dev/preview_server.py
"""
import asyncio
import json
import os
import secrets
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "autopilot"))
SECRET = secrets.token_hex(24)
os.environ.update(AUTOPILOT_DATA_DIR=tempfile.mkdtemp(prefix="vellur-preview-"), AUTOPILOT_ADMIN_SECRET=SECRET,
                  AUTOPILOT_LIVE="0", AGENT_RUNNER_URL="", AGENT_RUNNER_SECRET="", VAST_API_KEY="", LITELLM_MASTER_KEY="")

import uvicorn  # noqa: E402

from app.agent import AgentManager  # noqa: E402
from app.api import build_admin  # noqa: E402
from app.main import build  # noqa: E402
from app.router import Router  # noqa: E402


def seed(db) -> None:
    """A little demo content so the screens are not empty."""
    now = time.time()
    cur = db.x("INSERT INTO conversations(title,model,created,updated) VALUES(?,?,?,?)", ("Demo conversation", "vellur-chat", now, now))
    for role, text in (("user", "What can you do?"), ("assistant", "This is the **staging preview** of VELLUR AI Core. "
                                                                  "Nothing here is connected to real models or data.")):
        db.x("INSERT INTO messages(conv_id,role,content,created) VALUES(?,?,?,?)", (cur.lastrowid, role, text, now))
    cur = db.x("INSERT INTO tasks(created,updated,state,repo,base,model,prompt,outcome,runs,files_changed,started,ended) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (now, now, "done", "demo/project", "main", "vellur-coder",
                                                   "Demo task: restyle the landing page", "completed", 1, 1, now, now))
    tid = cur.lastrowid
    report = {"run": 1, "outcome": "completed", "why": "Demo report shown in the staging preview.", "sections": {}, "tests":
              [{"command": "npm test", "exit": 0, "status": "completed", "test": True}], "files":
              [{"path": "index.html", "status": "M", "added": 12, "removed": 3}], "shots": [], "errors": [],
              "cost_run_usd": 0.0123, "cost_total_usd": 0.0123, "tokens": {"input": 1200, "output": 340}, "tool_calls": 4}
    events = [("user", {"text": "Demo task: restyle the landing page"}), ("status", {"text": "Agent started", "state": "running"}),
              ("text", {"text": "I'll look at the current page first."}),
              ("tool", {"tool": "read", "status": "completed", "input": {"filePath": "index.html"}, "output": "", "title": "index.html"}),
              ("tool", {"tool": "edit", "status": "completed", "input": {"filePath": "index.html", "oldString": "<h1>Old</h1>",
                                                                         "newString": "<h1>New</h1>"}, "output": ""}),
              ("tool", {"tool": "bash", "status": "completed", "input": {"command": "npm test", "description": "Run the tests"},
                        "output": "12 passed", "exit": 0}),
              ("text", {"text": "## Summary\nThe page is restyled.\n## Done\n- New heading\n## Not done\nnothing\nSTATUS: COMPLETE"}),
              ("report", report)]
    for kind, data in events:
        db.x("INSERT INTO task_events(task_id,ts,run,kind,data) VALUES(?,?,?,?,?)", (tid, now, 1, kind, json.dumps(data)))
    db.x("UPDATE tasks SET report=? WHERE id=?", (json.dumps(report), tid))


class AsOwner:
    """Lets the previewed page act as the signed-in owner of this throwaway instance (it has no real data or keys)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == "/":
            await send({"type": "http.response.start", "status": 302, "headers": [(b"location", b"/autopilot/")]})
            return await send({"type": "http.response.body", "body": b""})
        if path == "/autopilot/api/auth/me":
            body = json.dumps({"logged_in": True, "must_change": False, "password_set": True, "preview": True}).encode()
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
            return await send({"type": "http.response.body", "body": body})
        headers = [(k, v) for k, v in scope["headers"] if k not in (b"origin", b"x-vellur-admin", b"cookie")]
        headers.append((b"x-vellur-admin", SECRET.encode()))
        await self.app(dict(scope, headers=headers), receive, send)


async def main() -> None:
    ctrl = build()
    seed(ctrl.db)
    router = Router(ctrl)
    app = AsOwner(build_admin(ctrl, router=router, agent=AgentManager(ctrl, router)))
    port = int(os.environ.get("PORT", "4173"))
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()


if __name__ == "__main__":
    asyncio.run(main())
