"""NOVA timeline: derived from authoritative sources, idempotent, replayable; projects overview."""
import json

import httpx

from app.agent import AgentManager
from app.api import build_admin
from app.router import Router
from app.timeline import Timeline

ADMIN = {"X-Vellur-Admin": "s" * 32}


def seed(db):
    db.x("INSERT INTO tasks(id,created,updated,state,repo,base,branch,model,prompt,outcome,files_changed,pr_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
         (1, 10, 20, "done", "o/shop", "main", "", "vellur-coder", "Add a hero", "completed", 1, "https://github.com/o/shop/pull/4"))
    db.kv_set("conversation_task_7", 1)
    for ts, kind, data in ((11, "user", {"text": "Add a hero"}),
                           (12, "tool", {"tool": "read", "status": "completed", "input": {"filePath": "a"}}),
                           (13, "tool", {"tool": "edit", "status": "completed", "input": {"filePath": "/w/index.html"}}),
                           (14, "tool", {"tool": "bash", "status": "completed", "exit": 0, "input": {"command": "npm test"}}),
                           (15, "report", {"outcome": "completed", "files": [{"path": "index.html"}], "tests": [{}], "cost_run_usd": 0.01}),
                           (16, "pr", {"url": "https://github.com/o/shop/pull/4"})):
        db.x("INSERT INTO task_events(task_id,ts,run,kind,data) VALUES(?,?,?,?,?)", (1, ts, 1, kind, json.dumps(data)))
    db.x("INSERT INTO routing_decisions(ts,tag,role,mode,policy,chosen,reason,est_usd) VALUES(?,?,?,?,?,?,?,?)",
         (11.5, "task:1", "vellur-coder", "auto", "balanced", "p:m", "why", 0.002))


def test_sync_is_idempotent_and_replayable(rig):
    ctrl = rig()
    seed(ctrl.db)
    t = Timeline(ctrl.db)
    assert t.sync() == 6                     # request, edit, check, run result, PR, model choice (the read is noise)
    assert t.sync() == 0
    ctrl.db.x("DELETE FROM kv WHERE key LIKE 'timeline_mark_%'")   # watermarks lost: replay adds nothing twice
    assert t.sync() == 0
    rows = t.query(project="o/shop")
    assert [r["event_type"] for r in rows] == ["pr_opened", "run_result", "check", "file_change", "model_selected", "request"]
    assert all(r["task_id"] == 1 for r in rows) and rows[-1]["conv_id"] == 7
    assert [r["category"] for r in t.query(category="Cost")] == ["Cost"]
    d = {"repo": "o/shop", "base": "main", "pr": {"number": 4, "url": "u"}, "merge_sha": "c" * 40, "installed": "c" * 40,
         "stages": [{"key": "review", "status": "done"}, {"key": "merged", "status": "done"}, {"key": "deployed", "status": "done"}]}
    t.observe_delivery({"id": 1, "repo": "o/shop"}, d)
    t.observe_delivery({"id": 1, "repo": "o/shop"}, d)
    assert [r["event_type"] for r in t.query(category="Release")][:3].count("merged") == 1
    assert {"pr_approved", "merged", "deployed"} <= {r["event_type"] for r in t.query()}


async def test_project_overview_answers_what_happened(rig):
    ctrl = rig()
    seed(ctrl.db)
    router = Router(ctrl, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=build_admin(ctrl, router=router, agent=AgentManager(ctrl, router))),
                          base_url="https://ai.example")
    p = (await c.get("/autopilot/api/projects", headers=ADMIN)).json()["projects"]
    assert p[0]["repo"] == "o/shop" and p[0]["tasks"] == 1 and p[0]["last"]["outcome"] == "completed"
    o = (await c.get("/autopilot/api/projects/overview?repo=o/shop", headers=ADMIN)).json()
    assert o["last"]["conversation"] == 7 and o["last"]["pr_url"].endswith("/4") and o["events"]
    assert (await c.get("/autopilot/api/projects/overview?repo=o/none", headers=ADMIN)).status_code == 404
    tl = (await c.get("/autopilot/api/timeline?task=1", headers=ADMIN)).json()
    assert len(tl["events"]) == 6 and "Release" in tl["categories"]
