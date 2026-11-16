"""Staging preview of the VELLUR dashboard, used when VELLUR (or anyone) works on VELLUR itself.

It starts the real controller app from THIS checkout with an empty demo database in a temporary folder, no provider
keys, no GPU, an in-memory simulated agent runner and dry-run mode, and lets the previewed page act as a signed-in owner so every screen
can be looked at. It never touches the production data or the production process, and install.sh does not copy this
file to the server.

    PORT=4173 bash dev/preview.sh
"""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import struct
import sys
import tempfile
import time
import zlib

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "autopilot"))
SECRET = secrets.token_hex(24)
os.environ.update(AUTOPILOT_DATA_DIR=tempfile.mkdtemp(prefix="vellur-preview-"), AUTOPILOT_ADMIN_SECRET=SECRET,
                  AUTOPILOT_LIVE="0", AGENT_RUNNER_URL="", AGENT_RUNNER_SECRET="", VAST_API_KEY="", LITELLM_MASTER_KEY="")

import uvicorn  # noqa: E402

from demo_runner import DemoRunner
from dataclasses import replace

from app.agent import AgentManager  # noqa: E402
from app.api import build_admin  # noqa: E402
from app.images import ImageResult  # noqa: E402
from app.main import build  # noqa: E402
from app.model_intel import infer_capabilities, labels  # noqa: E402
from app.router import Router  # noqa: E402


def seed(db) -> tuple[int, int]:
    """A little demo content so the screens are not empty."""
    now = time.time()
    cur = db.x("INSERT INTO conversations(title,model,created,updated) VALUES(?,?,?,?)", ("Demo conversation", "vellur-chat", now, now))
    cid = cur.lastrowid
    for role, text in (("user", "What can you do?"), ("assistant", "This is the **staging preview** of NOVA. "
                                                           "Nothing here is connected to real models or data.")):
        db.x("INSERT INTO messages(conv_id,role,content,created) VALUES(?,?,?,?)", (cid, role, text, now))
    cur = db.x("INSERT INTO tasks(created,updated,state,repo,base,model,prompt,outcome,runs,files_changed,started,ended) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (now, now, "done", "demo/project", "main", "vellur-coder",
                                                   "Demo task: restyle the landing page", "completed", 1, 1, now, now))
    tid = cur.lastrowid
    report = {"run": 1, "outcome": "completed", "why": "Demo report shown in the staging preview.", "sections": {}, "tests":
              [{"command": "npm test", "exit": 0, "status": "completed", "test": True}], "files":
              [{"path": "index.html", "status": "M", "added": 12, "removed": 3}], "shots": [], "errors": [],
              "cost_run_usd": 0.0, "cost_total_usd": 0.0, "tokens": {"input": 1200, "output": 340}, "tool_calls": 4}
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
    db.kv_set(f"conversation_task_{cid}", tid)
    from app.knowledge import Knowledge
    k = Knowledge(db)
    k.create({"project": "demo/project", "namespace": "Decisions", "status": "decided", "title": "Landing page keeps one call to action",
              "body": "Demo record: the owner decided the landing page has exactly one primary button."}, source="owner (demo)")
    k.create({"namespace": "Constraints", "title": "No localStorage in dashboards",
              "body": "Demo record: dashboards keep state on the server or in cookies, never in localStorage."}, source="owner (demo)")
    k.record_run({"id": tid, "repo": "demo/project", "prompt": "Demo task: restyle the landing page"}, 1, report)
    db.x("INSERT INTO music_releases(title,artist,status,target_date,notes,created,updated) VALUES(?,?,?,?,?,?,?)",
         ("Midnight Frequencies", "Ring City", "planned", "2026-12-04", "Demo release: the first single of the season, three tracks.", now, now))
    rel = db.all("SELECT id FROM music_releases ORDER BY id DESC LIMIT 1")[0]["id"]
    for i, (t, d) in enumerate((("Neon Hours", "3:21"), ("Low Orbit", "2:58"), ("Signal Bloom", "4:02")), 1):
        db.x("INSERT INTO music_tracks(release_id,title,duration,position,created) VALUES(?,?,?,?,?)", (rel, t, d, i, now))

    # 3D Studio scenes
    for i, (title, desc) in enumerate((
        ("Robot arm", "6-DOF articulated arm with IK targets, ready for animation"),
        ("Scene layout", "Modular room with procedural furniture placement"),
        ("Character base", "Low-poly character with rigging helpers"),
    ), 1):
        db.x("INSERT INTO studio_scenes(title,description,version,task_id,created,updated) VALUES(?,?,?,?,?,?)",
             (title, desc, "1.0", tid, now - i * 3600, now - i * 3600))

    # Self-dev runs
    for i, (title, goal, stage) in enumerate((
        ("Add dark mode", "Implement dark/light theme toggle with persistence", "TEST"),
        ("Refactor API routes", "Split monolithic router into feature modules", "REVIEW"),
        ("Update dependencies", "Bump all deps to latest compatible versions", "COMPLETED"),
    ), 1):
        db.x("INSERT INTO selfdev_runs(title,goal,stage,target_paths,agent_task_id,created,updated) VALUES(?,?,?,?,?,?,?)",
             (title, goal, stage, json.dumps(["autopilot/app/api.py", "autopilot/app/router.py"]), tid + i, now - i * 7200, now - i * 7200))

    # Budget with cumulative €10 cap (using USD values, ~€10 = $10.80)
    db.x("INSERT INTO ledger(ts,provider,model,cost_usd,confirmed,request_id,created) VALUES(?,?,?,?,?,?,?)",
         (now, "hyperai", "RedHatAI/Qwen3.8-27B-INT4", 2.5, 1, "demo-req-1", now))
    db.x("INSERT INTO ledger(ts,provider,model,cost_usd,confirmed,request_id,created) VALUES(?,?,?,?,?,?,?)",
         (now, "vastai", "meta-llama/Llama-3.1-70B", 3.2, 1, "demo-req-2", now))
    db.x("INSERT INTO ledger(ts,provider,model,cost_usd,confirmed,request_id,created) VALUES(?,?,?,?,?,?,?)",
         (now, "hyperai", "RedHatAI/Qwen3.8-27B-INT4", 1.8, 0, "demo-req-3", now))

    # Events for live activity feed
    for kind, data in (
        ("info", {"text": "GPU session started on HyperAI H100"}),
        ("info", {"text": "Agent task completed: Add dark mode"}),
        ("info", {"text": "3D scene saved: Robot arm v1.2"}),
        ("info", {"text": "Self-dev run finished: Update dependencies"}),
    ):
        db.event(kind, "mission", data["text"])

    return cid, tid


def demo_png(prompt: str, width: int = 512, height: int = 512) -> bytes:
    """A real PNG made locally, so the preview never needs an image API."""
    digest = hashlib.sha256(str(prompt or "preview").encode()).digest()
    r0, g0, b0, r1, g1, b1 = digest[:6]
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            mix = (x / max(1, width - 1) + y / max(1, height - 1)) / 2
            r, g, b = int(r0 + (r1 - r0) * mix), int(g0 + (g1 - g0) * mix), int(b0 + (b1 - b0) * mix)
            if ((x // 48) + (y // 48)) % 2:
                r, g, b = int(r * 0.78), int(g * 0.78), int(b * 0.78)
            if x < 6 or y < 6 or x >= width - 6 or y >= height - 6:
                r = g = b = 245
            row += bytes((r, g, b))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class DemoUpstream:
    """OpenAI-shaped fake provider used only by the preview: no network, no keys, no cost."""

    def __init__(self):
        self.calls = []

    def handler(self, req: httpx.Request):
        body = json.loads(req.content or b"{}") if req.method == "POST" else {}
        self.calls.append((req.url.path, body))
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "demo-chat", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                 "supported_parameters": ["tools", "reasoning"], "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "demo-image", "architecture": {"input_modalities": ["text"], "output_modalities": ["image", "text"]},
                 "pricing": {"prompt": "0", "completion": "0"}}]})
        if req.url.path.endswith("/images/generations"):
            prompt = str(body.get("prompt") or "preview image")
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(demo_png(prompt)).decode()}],
                                             "usage": {"cost": 0.02}})
        if req.url.path.endswith("/chat/completions"):
            messages = body.get("messages") or []
            prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
            if body.get("model") == "demo-image" or set(body.get("modalities") or []) & {"image", "image_url"}:
                url = "data:image/png;base64," + base64.b64encode(demo_png(prompt)).decode()
                return httpx.Response(200, json={"choices": [{"message": {"content": "Preview image",
                                                                         "images": [{"image_url": {"url": url}}]}}],
                                                 "usage": {"cost": 0.02}})
            answer = ("This is the **staging preview** answer. The demo provider is local: no API key, no real model "
                      "and no cost. Configure a real provider in Models to use NOVA for real work.")
            if body.get("stream"):
                chunks = [{"choices": [{"delta": {"content": answer}}]},
                          {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 28}}]
                text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}],
                                             "usage": {"prompt_tokens": 12, "completion_tokens": 28}})
        return httpx.Response(404, json={"error": {"message": "unknown preview endpoint"}})


def configure_demo(ctrl, router: Router) -> None:
    router.update_provider("preview", {"display": "Preview demo (no API)", "base_url": "https://preview.invalid/v1",
                                       "plan": "per_token", "api_key": "preview-demo-key"})
    rows, meta = [], {}
    for mid, image, ctx in (("demo-chat", False, 32000), ("demo-image", True, 0)):
        raw = {"architecture": {"input_modalities": ["text"],
                                "output_modalities": ["image", "text"] if image else ["text"]},
               "supported_parameters": ["tools", "reasoning"] if not image else []}
        caps = infer_capabilities(mid, raw, "preview")
        rows.append({"id": mid, "in": 0.0, "out": 0.0, "ctx": ctx})
        meta[mid] = {"capabilities": caps, "labels": labels(caps), "image_price": 0.02 if image else None,
                     "input": 0.0, "output": 0.0, "context": ctx}
    router.db.kv_set("llm_catalog_preview", {"ts": time.time(), "models": rows})
    router.db.kv_set("llm_catalog_meta_preview", {"ts": time.time(), "models": meta})
    ctrl.settings.update({"routes": {"vellur-coder": ["preview:demo-chat"], "vellur-chat": ["preview:demo-chat"],
                                     "vellur-max": ["preview:demo-chat"], "vellur-image": ["preview:demo-image"]},
                          "prices": {"preview:demo-chat": [0, 0]},
                          "image_prices": {"preview:demo-image": 0.02},
                          "image_confirm_paid": True, "agent_image_generation": True, "agent_auto_publish": False,
                          "auto_allowance_usd": 1.0})


def seed_demo_image(app, db, cid: int, tid: int) -> None:
    store = app.state.images.store
    row = store.save(ImageResult(demo_png("Demo generated image"), "image/png", 512, 512),
                     "Demo generated image", "preview:demo-image", "preview", "demo-image", 0.0, "plan",
                     conv_id=cid, task_id=tid)
    db.x("INSERT INTO messages(conv_id,role,content,created,meta) VALUES(?,?,?,?,?)",
         (cid, "assistant", "Generated image (preview demo, no real provider)", time.time(),
          json.dumps({"images": [store.public(row)], "model": "vellur-image", "target": "preview:demo-image",
                      "cost_usd": 0.0, "cost_source": "plan"})))
    db.x("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))


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
        async def marked_send(message):
            if path == "/autopilot/":
                if message["type"] == "http.response.start":
                    message = dict(message, headers=[(k, v) for k, v in message.get("headers", []) if k != b"content-length"])
                elif message["type"] == "http.response.body":
                    badge = b'<div style="position:fixed;bottom:3px;right:8px;z-index:100;pointer-events:none;font:9px monospace;color:var(--muted);background:var(--bg);padding:3px 6px;border-radius:3px">DEMO / SIMULATED SERVICES</div>'
                    message = dict(message, body=message.get("body", b"").replace(b"</body>", badge + b"</body>"))
            await send(message)
        await self.app(dict(scope, headers=headers), receive, marked_send)


async def main() -> None:
    ctrl = build()
    ctrl.env = replace(ctrl.env, agent_runner_url="http://demo.invalid", agent_runner_secret="preview-only-runner-secret-000")
    cid, tid = seed(ctrl.db)
    upstream = DemoUpstream()
    router = Router(ctrl, transport=httpx.MockTransport(upstream.handler))
    configure_demo(ctrl, router)
    runner = DemoRunner()
    manager = AgentManager(ctrl, router, runner_transport=httpx.MockTransport(runner.handler),
                           github_transport=httpx.MockTransport(lambda req: httpx.Response(403)), poll_seconds=0.5)
    admin_app = build_admin(ctrl, router=router, agent=manager)
    seed_demo_image(admin_app, ctrl.db, cid, tid)
    app = AsOwner(admin_app)
    port = int(os.environ.get("PORT", "4173"))
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()


if __name__ == "__main__":
    asyncio.run(main())
