"""NOVA unified event timeline (blueprint §16) and project overviews.

One table (`timeline`) links requests, runs, file changes, checks, routing decisions (cost), pull requests, merges,
deployments, failures and recoveries by stable ids. It is *derived*: every row comes from an authoritative source
(task_events, routing_decisions, the controller's event log, GitHub/installer observations) and is keyed by an
`event_id` built from that source's own id, so ingestion is idempotent (INSERT OR IGNORE) and can be replayed after
any interruption: `sync()` simply runs again. Watermarks only make it cheaper; losing them loses nothing.
GitHub (PR, merge) and the installer (deployed commit) stay independently authoritative: their rows are written only
from what `Release.delivery()` actually observed.
"""
from __future__ import annotations

import json
import time
from typing import Optional

CATEGORIES = ("Work", "Code", "Release", "Cost", "System")
TEST_RE = ("test", "pytest", "vitest", "jest", "check", "lint", "tsc", "build")


class Timeline:
    def __init__(self, db):
        self.db = db

    def _put(self, event_id: str, occurred: float, source: str, etype: str, category: str, title: str, *,
             task_id: Optional[int] = None, project: str = "", conv_id: Optional[int] = None, payload: Optional[dict] = None,
             ref: str = "", correlation: str = "") -> None:
        self.db.x("INSERT OR IGNORE INTO timeline(event_id,project,task_id,conv_id,correlation_id,occurred_at,observed_at,"
                  "source,event_type,category,title,payload,external_ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (event_id, project, task_id, conv_id, correlation or (f"task:{task_id}" if task_id else ""), occurred,
                   time.time(), source, etype, category, title[:300], json.dumps(payload or {}, default=str)[:4000], ref[:300]))

    def _mark(self, key: str, default: int = 0) -> int:
        return int(self.db.kv_get(f"timeline_mark_{key}", default) or default)

    # ── ingestion ───────────────────────────────────────────────────────────────────────────────
    def sync(self) -> int:
        before = (self.db.one("SELECT COUNT(*) AS n FROM timeline") or {}).get("n", 0)
        repos = {r["id"]: r for r in self.db.all("SELECT id,repo,prompt FROM tasks")}
        conv = {}
        for r in self.db.all("SELECT key,value FROM kv WHERE key LIKE 'conversation_task_%'"):
            try:
                conv[int(json.loads(r["value"]))] = int(r["key"].rsplit("_", 1)[1])
            except (ValueError, TypeError):
                pass
        mark = self._mark("te")
        last = mark
        for e in self.db.all("SELECT id,task_id,ts,run,kind,data FROM task_events WHERE id>? ORDER BY id LIMIT 5000", (mark,)):
            last = e["id"]
            t = repos.get(e["task_id"]) or {}
            d = json.loads(e["data"] or "{}")
            common = {"task_id": e["task_id"], "project": t.get("repo") or "", "conv_id": conv.get(e["task_id"])}
            k, eid = e["kind"], f"te:{e['id']}"
            if k == "user":
                self._put(eid, e["ts"], "agent", "request", "Work",
                          ("Asked: " if e["run"] == 1 else "Follow-up: ") + " ".join((d.get("text") or "").split())[:160],
                          payload={"run": e["run"]}, **common)
            elif k == "tool":
                tool, inp = d.get("tool"), d.get("input") or {}
                if tool in ("edit", "write", "multiedit") and d.get("status") == "completed":
                    self._put(eid, e["ts"], "agent", "file_change", "Code",
                              ("Wrote " if tool == "write" else "Edited ") + str(inp.get("filePath") or "")[-120:],
                              payload={"run": e["run"], "tool": tool}, **common)
                elif tool == "bash" and d.get("status") in ("completed", "error") and any(w in (inp.get("command") or "") for w in TEST_RE):
                    ok = d.get("exit") == 0
                    self._put(eid, e["ts"], "agent", "check", "Code",
                              ("Check passed: " if ok else "Check failed: ") + str(inp.get("command") or "")[:140],
                              payload={"run": e["run"], "exit": d.get("exit")}, **common)
            elif k == "report":
                cost = d.get("cost_run_usd")
                self._put(eid, e["ts"], "agent", "run_result", "Work",
                          f"Run {e['run']}: {d.get('outcome') or 'not confirmed'}" + (f" · {len(d.get('files') or [])} files" if d.get("files") else ""),
                          payload={"run": e["run"], "outcome": d.get("outcome"), "why": d.get("why"), "cost_run_usd": cost,
                                   "tests": len(d.get("tests") or [])}, **common)
            elif k == "pr" and d.get("url"):
                self._put(eid, e["ts"], "agent", "pr_opened" if not d.get("updated") else "pr_updated", "Release",
                          ("Pull request updated" if d.get("updated") else "Pull request opened"), ref=d["url"], **common)
            elif k == "publication":
                self._put(eid, e["ts"], "agent", "publication", "Release",
                          f"Automatic publishing {d.get('status') or 'unknown'}" + (f" · {str(d.get('commit'))[:7]}" if d.get("commit") else ""),
                          payload={k2: d.get(k2) for k2 in ("status", "branch", "commit", "reason")}, **common)
            elif k == "error":
                self._put(eid, e["ts"], "agent", "failure", "System", "Failure: " + str(d.get("message") or d.get("text") or "")[:200], **common)
            elif k == "status" and d.get("state") in ("failed", "cancelled", "timeout", "over_budget"):
                self._put(eid, e["ts"], "agent", "run_" + d["state"], "System", str(d.get("text") or d["state"])[:200], **common)
        if last != mark:
            self.db.kv_set("timeline_mark_te", last)
        mark = last = self._mark("rd")
        for r in self.db.all("SELECT * FROM routing_decisions WHERE id>? ORDER BY id LIMIT 5000", (mark,)):
            last = r["id"]
            tid = int(r["tag"].split(":")[1]) if r["tag"].startswith("task:") and r["tag"].split(":")[1].isdigit() else None
            cid = int(r["tag"].split(":")[1]) if r["tag"].startswith("chat:") and r["tag"].split(":")[1].isdigit() else None
            self._put(f"rd:{r['id']}", r["ts"], "router", "model_selected", "Cost",
                      f"{r['policy'].capitalize()} routing chose {r['chosen'] or 'no model'}" + (f" · ≈${r['est_usd']:.4f}" if r["est_usd"] else ""),
                      task_id=tid, conv_id=cid, project=(repos.get(tid) or {}).get("repo", "") if tid else "",
                      payload={"decision": r["id"], "mode": r["mode"], "reason": r["reason"]})
        if last != mark:
            self.db.kv_set("timeline_mark_rd", last)
        mark = last = self._mark("ev")
        for r in self.db.all("SELECT * FROM events WHERE id>? AND kind IN ('release','llm') ORDER BY id LIMIT 5000", (mark,)):
            last = r["id"]
            self._put(f"ev:{r['id']}", r["ts"], "controller", r["kind"], "Release" if r["kind"] == "release" else "System",
                      r["message"], payload={"level": r["level"]})
        if last != mark:
            self.db.kv_set("timeline_mark_ev", last)
        return (self.db.one("SELECT COUNT(*) AS n FROM timeline") or {}).get("n", 0) - before

    def observe_delivery(self, task: dict, d: dict) -> None:
        """Rows for what GitHub / the installer confirmed. Keys are the external facts, so repeats are ignored."""
        common = {"task_id": task["id"], "project": task.get("repo") or ""}
        st = {x["key"]: x["status"] for x in d.get("stages") or []}
        pr = d.get("pr") or {}
        now = time.time()
        if st.get("review") == "done" and pr:
            self._put(f"gh:{d['repo']}#{pr['number']}:approved", now, "github", "pr_approved", "Release",
                      f"PR #{pr['number']} approved", ref=pr.get("url", ""), **common)
        if st.get("merged") == "done" and d.get("merge_sha"):
            self._put(f"gh:{d['repo']}@{d['merge_sha']}:merged", now, "github", "merged", "Release",
                      f"Merged into {d.get('base') or 'base'} as {d['merge_sha'][:7]}", ref=pr.get("url", ""), **common)
        if st.get("deployed") == "done" and d.get("installed"):
            self._put(f"deploy:{d['installed']}:{task['id']}", now, "installer", "deployed", "Release",
                      f"Live: the server runs {d['installed'][:7]}", **common)

    # ── queries ─────────────────────────────────────────────────────────────────────────────────
    def query(self, project: str = "", task_id: Optional[int] = None, category: str = "", limit: int = 100,
              before: Optional[float] = None) -> list[dict]:
        where, args = [], []
        if project:
            where.append("project=?")
            args.append(project)
        if task_id:
            where.append("task_id=?")
            args.append(task_id)
        if category in CATEGORIES:
            where.append("category=?")
            args.append(category)
        if before:
            where.append("occurred_at<?")
            args.append(before)
        rows = self.db.all("SELECT * FROM timeline" + (" WHERE " + " AND ".join(where) if where else "")
                           + " ORDER BY occurred_at DESC, id DESC LIMIT ?", (*args, min(limit, 500)))
        for r in rows:
            r["payload"] = json.loads(r["payload"] or "{}")
        return rows

    def projects(self, tag_costs) -> list[dict]:
        out = []
        for p in self.db.all("SELECT repo, COUNT(*) AS tasks, MAX(updated) AS updated FROM tasks GROUP BY repo ORDER BY updated DESC LIMIT 50"):
            ids = [r["id"] for r in self.db.all("SELECT id FROM tasks WHERE repo=?", (p["repo"],))]
            last = self.db.one("SELECT id,prompt,state,outcome,pr_url,updated FROM tasks WHERE repo=? ORDER BY updated DESC LIMIT 1", (p["repo"],))
            cost = {"confirmed_usd": 0.0, "estimated_usd": 0.0, "unpriced_requests": 0}
            for i in ids:
                c = tag_costs(f"task:{i}")
                for k in cost:
                    cost[k] += c[k]
            out.append({"repo": p["repo"], "name": p["repo"] or "Empty workspace", "tasks": p["tasks"], "updated": p["updated"],
                        "last": last, "cost": {k: round(v, 4) if isinstance(v, float) else v for k, v in cost.items()},
                        "knowledge": (self.db.one("SELECT COUNT(*) AS n FROM knowledge WHERE project=?", (p["repo"],)) or {}).get("n", 0)})
        return out
