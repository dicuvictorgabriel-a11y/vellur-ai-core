"""NOVA Knowledge (blueprint §23): durable, searchable project memory the agent reads before it works.

Storage: the `knowledge` table plus an SQLite FTS5 index (falls back to LIKE where FTS5 is missing). No vector database:
full-text search is enough for a few thousand records; semantic retrieval can be added when a measured need appears.

Rules
- Scoped: `project` is a repository (owner/name) or "" for workspace-wide records.
- Traceable: every record has a source ("owner", "task:12 run 2", …); records written by NOVA say so.
- Protected: edits must send the version they read (409 on a stale write); locked records cannot be changed or deleted
  until unlocked by the owner.
- No secrets: text that looks like a key, token or private key is refused.
- Only verified facts are written automatically: a run whose outcome is "completed" AND whose checks all passed
  (at least one) becomes an "Implementation history" record; a pull request link is added when it exists.
"""
from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import time
from typing import Optional

NAMESPACES = ("Product", "Architecture", "Decisions", "Design System", "Constraints", "Operations", "Known Issues",
              "Implementation history", "Model Economics")
STATUSES = ("implemented", "proposed", "deprecated", "decided")
SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,}")
STOP = set("the and for with that this from into your you are was were have has will can not but all any its our out "
           "use make please add new should would could about what when where which there their them then than also "
           "just like want need some more most very".split())


class KnowledgeError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Knowledge:
    def __init__(self, db):
        self.db = db
        self.fts = True
        try:
            db.x("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(title, body, tags, namespace, "
                 "content='knowledge', content_rowid='id', tokenize='unicode61 remove_diacritics 2')")
            for name, sql in (
                    ("knowledge_ai", "AFTER INSERT ON knowledge BEGIN INSERT INTO knowledge_fts(rowid,title,body,tags,namespace) "
                                     "VALUES (new.id,new.title,new.body,new.tags,new.namespace); END"),
                    ("knowledge_ad", "AFTER DELETE ON knowledge BEGIN INSERT INTO knowledge_fts(knowledge_fts,rowid,title,body,tags,namespace) "
                                     "VALUES ('delete',old.id,old.title,old.body,old.tags,old.namespace); END"),
                    ("knowledge_au", "AFTER UPDATE ON knowledge BEGIN INSERT INTO knowledge_fts(knowledge_fts,rowid,title,body,tags,namespace) "
                                     "VALUES ('delete',old.id,old.title,old.body,old.tags,old.namespace); INSERT INTO knowledge_fts"
                                     "(rowid,title,body,tags,namespace) VALUES (new.id,new.title,new.body,new.tags,new.namespace); END")):
                db.x(f"CREATE TRIGGER IF NOT EXISTS {name} {sql}")
        except sqlite3.OperationalError:
            self.fts = False

    # ── validation ──────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _clean(data: dict, partial: bool = False) -> dict:
        out = {}
        for k, limit in (("title", 200), ("body", 20000), ("tags", 300), ("paths", 1000), ("source", 200), ("project", 140)):
            if k in data:
                v = str(data[k] or "").strip()
                if len(v) > limit:
                    raise KnowledgeError("invalid", f"{k} is limited to {limit} characters")
                out[k] = v
        if "namespace" in data:
            if data["namespace"] not in NAMESPACES:
                raise KnowledgeError("invalid", "namespace must be one of: " + ", ".join(NAMESPACES))
            out["namespace"] = data["namespace"]
        if "status" in data:
            if data["status"] not in STATUSES:
                raise KnowledgeError("invalid", "status must be one of: " + ", ".join(STATUSES))
            out["status"] = data["status"]
        if out.get("project") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}", out["project"]):
            raise KnowledgeError("invalid", "project must be empty (workspace-wide) or owner/repository")
        if not partial:
            for k in ("title", "body", "namespace"):
                if not out.get(k):
                    raise KnowledgeError("invalid", f"{k} is required")
        for k in ("title", "body", "tags", "paths"):
            if k in out and SECRET.search(out[k]):
                raise KnowledgeError("secret", "this looks like a key, token or private key; Knowledge never stores secrets")
        return out

    # ── CRUD ────────────────────────────────────────────────────────────────────────────────────
    def get(self, kid: int) -> Optional[dict]:
        return self.db.one("SELECT * FROM knowledge WHERE id=?", (kid,))

    def create(self, data: dict, source: str = "owner") -> dict:
        d = self._clean(data)
        now = time.time()
        cur = self.db.x("INSERT INTO knowledge(project,namespace,title,body,status,source,tags,paths,created,updated) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (d.get("project", ""), d["namespace"], d["title"], d["body"], d.get("status", "implemented"),
                         d.get("source") or source, d.get("tags", ""), d.get("paths", ""), now, now))
        return self.get(int(cur.lastrowid))

    def update(self, kid: int, data: dict, version: int) -> dict:
        row = self.get(kid)
        if not row:
            raise KnowledgeError("not_found", "unknown record", 404)
        if row["locked"]:
            raise KnowledgeError("locked", "this record is locked; unlock it first", 409)
        if int(version) != int(row["version"]):
            raise KnowledgeError("conflict", "this record changed since you opened it; reload and apply your edit again", 409)
        d = self._clean(data, partial=True)
        if not d:
            return row
        sets = ", ".join(f"{k}=?" for k in d)
        cur = self.db.x(f"UPDATE knowledge SET {sets}, updated=?, version=version+1 WHERE id=? AND version=?",
                        (*d.values(), time.time(), kid, row["version"]))
        if not cur.rowcount:
            raise KnowledgeError("conflict", "this record changed since you opened it; reload and apply your edit again", 409)
        return self.get(kid)

    def delete(self, kid: int) -> None:
        row = self.get(kid)
        if not row:
            raise KnowledgeError("not_found", "unknown record", 404)
        if row["locked"]:
            raise KnowledgeError("locked", "this record is locked; unlock it first", 409)
        self.db.x("DELETE FROM knowledge WHERE id=?", (kid,))

    def lock(self, kid: int, locked: bool) -> dict:
        if not self.get(kid):
            raise KnowledgeError("not_found", "unknown record", 404)
        self.db.x("UPDATE knowledge SET locked=? WHERE id=?", (1 if locked else 0, kid))
        return self.get(kid)

    # ── search / retrieval ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _terms(text: str, limit: int = 12) -> list[str]:
        seen: list[str] = []
        for w in WORD.findall(text or ""):
            w = w.lower().strip("._-")
            if len(w) > 2 and w not in STOP and w not in seen:
                seen.append(w)
        return seen[:limit]

    def search(self, q: str = "", project: Optional[str] = None, namespace: str = "", limit: int = 50,
               any_term: bool = False) -> list[dict]:
        where, args = [], []
        if project is not None:
            where.append("(k.project=? OR k.project='')")
            args.append(project)
        if namespace:
            where.append("k.namespace=?")
            args.append(namespace)
        terms = self._terms(q)
        if terms and self.fts:
            match = (" OR " if any_term else " AND ").join('"' + t.replace('"', "") + '"*' for t in terms)
            sql = ("SELECT k.*, bm25(knowledge_fts) AS rank, snippet(knowledge_fts, 1, '[', ']', ' … ', 18) AS snippet "
                   "FROM knowledge_fts JOIN knowledge k ON k.id=knowledge_fts.rowid WHERE knowledge_fts MATCH ?"
                   + "".join(" AND " + w for w in where) + " ORDER BY rank LIMIT ?")
            return self.db.all(sql, (match, *args, limit))
        if terms:
            for t in terms:
                where.append("(k.title LIKE ? OR k.body LIKE ? OR k.tags LIKE ?)")
                args += [f"%{t}%"] * 3
            if any_term and len(terms) > 1:      # LIKE fallback: OR over the term clauses
                n = len(terms)
                where = where[:-n] + ["(" + " OR ".join(where[-n:]) + ")"]
        sql = "SELECT k.* FROM knowledge k" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY k.updated DESC LIMIT ?"
        return self.db.all(sql, (*args, limit))

    def for_task(self, prompt: str, project: str, limit: int = 6, budget: int = 6000) -> tuple[str, list[int]]:
        """The records an agent should read before working: locked/decided ones for this project first, then the best
        full-text matches for the request. Returns (prompt block, ids)."""
        picked: dict[int, dict] = {}
        for r in self.db.all("SELECT * FROM knowledge WHERE (project=? OR project='') AND (locked=1 OR status='decided') "
                             "ORDER BY updated DESC LIMIT ?", (project, limit)):
            picked[r["id"]] = r
        for r in self.search(prompt, project=project, limit=limit, any_term=True):
            if len(picked) >= limit:
                break
            picked.setdefault(r["id"], r)
        if not picked:
            return "", []
        lines, used = [], 0
        for r in picked.values():
            text = f"- [{r['namespace']}] {r['title']} ({r['status']}, source: {r['source']}): {' '.join(r['body'].split())}"
            text = text[:1500]
            if used + len(text) > budget:
                break
            lines.append(text)
            used += len(text)
        block = ("Project knowledge (records kept by the owner and verified past work). Follow decided and locked "
                 "records; do not ask the owner again about what they settle. Report a conflict instead of silently "
                 "contradicting them:\n" + "\n".join(lines))
        return block, list(picked)[:len(lines)]

    # ── automatic, verified history ─────────────────────────────────────────────────────────────
    def record_run(self, task: dict, run: int, report: dict) -> Optional[dict]:
        """Persist a verified run (outcome completed and every reported check passed, at least one). Idempotent."""
        tests = report.get("tests") or []
        if report.get("outcome") != "completed" or not tests or any(t.get("exit") != 0 for t in tests):
            return None
        source = f"task:{task['id']} run {run}"
        if self.db.one("SELECT id FROM knowledge WHERE source=?", (source,)):
            return None
        files = [f.get("path", "") for f in report.get("files") or []][:30]
        body = (f"Request: {' '.join((task.get('prompt') or '').split())[:600]}\n"
                f"Result: {report.get('why') or 'completed'}\n"
                f"Files: {', '.join(files) or 'none reported'}\n"
                f"Checks passed: {', '.join(t.get('command', '') for t in tests)[:600]}")
        try:
            return self.create({"project": task.get("repo") or "", "namespace": "Implementation history",
                                "title": f"Task {task['id']}: {' '.join((task.get('prompt') or '').split())[:120]}",
                                "body": body, "status": "implemented", "paths": ", ".join(files)[:1000],
                                "tags": "verified,agent"}, source=source)
        except KnowledgeError:
            return None          # e.g. the request text contained something that looks like a secret

    def link_pr(self, task: dict, url: str) -> None:
        for r in self.db.all("SELECT id,body FROM knowledge WHERE source LIKE ?", (f"task:{task['id']} run %",)):
            if url not in r["body"]:
                self.db.x("UPDATE knowledge SET body=?, updated=?, version=version+1 WHERE id=?",
                          (r["body"] + f"\nPull request: {url}", time.time(), r["id"]))

    def ensure(self, source: str, record: dict) -> None:
        """Insert a record shipped with a release once (keyed by its source); never overwrites owner edits."""
        if not self.db.one("SELECT id FROM knowledge WHERE source=?", (source,)):
            with contextlib.suppress(KnowledgeError):
                self.create(record, source=source)

    def stats(self) -> dict:
        rows = self.db.all("SELECT namespace, COUNT(*) AS n FROM knowledge GROUP BY namespace")
        return {"namespaces": list(NAMESPACES), "statuses": list(STATUSES), "counts": {r["namespace"]: r["n"] for r in rows},
                "search": "fts5" if self.fts else "like"}


def dumps(rows: list) -> str:
    return json.dumps(rows, default=str)


# Records shipped with the recovery release (2026-11-16). Verified facts are "implemented"; the incident's root cause is
# "proposed" until the server's logs confirm it (docs/RECOVERY.md lists the commands).
RELEASE_RECORDS = [
    ("release:3d-studio-handover-2026-11-16", {
        "namespace": "Architecture", "status": "implemented", "title": "3D Studio: read the handover before UI work",
        "tags": "3d,studio,ui,handover", "paths": "docs/agent-handover/3d-studio-ui.md, autopilot/app/static/3d/, autopilot/app/studio3d.py",
        "body": "Before changing 3D Studio, read docs/agent-handover/3d-studio-ui.md: files and APIs (studio3d.py scenes, "
                "glb_builder.py GLB + preview PNG, static/3d/studio3d.js viewer, scene_studio.js editor), element ids and "
                "postMessage contracts to keep, verified status (procedural create/edit, save, GLB export, snapshots work; "
                "AI Assist fixed on 2026-11-16 (routed vellur-coder, explains fallbacks); no agent tool or vision loop; phone layout and "
                "NOVA themes not applied), and the acceptance checklist. The viewport is never themed. Precision Studio was removed."}),
    ("release:recovery-2026-11-16:architecture", {
        "namespace": "Architecture", "status": "implemented", "title": "Agent tasks survive controller restarts",
        "tags": "recovery,reliability,agent", "paths": "autopilot/app/agent.py, agent/runner.py, autopilot/app/router.py",
        "body": "The runner container executes the agent; the controller watches it. On controller start every in-flight "
                "task is recovered in the background: re-attach when the run is still going (events from the persisted "
                "cursor, persisted start time and cost), collect the result when it ended, or resume the same OpenCode "
                "session automatically (max 2 times, only with the workspace available and budget left) when the agent "
                "process was interrupted. Publications in progress are paused for the owner, never repeated. The agent's "
                "model token is persisted as a SHA-256 hash; the runner saves the session id as soon as it is known. "
                "Validated by tests/test_recovery_integration.py with the real runner process and SIGKILL mid-tool."}),
    ("release:recovery-2026-11-16:operations", {
        "namespace": "Operations", "status": "implemented", "title": "How to find out why the controller restarted",
        "tags": "recovery,diagnostics", "paths": "autopilot/app/health.py, docs/RECOVERY.md",
        "body": "Activity shows a boot event for every controller start: clean shutdown, or UNCLEAN stop (SIGKILL, "
                "out-of-memory kill or host crash) with the memory at the last heartbeat and whether the installed release "
                "changed. GET /autopilot/api/diagnostics/restarts lists the last 30 boots and the interrupted tasks. On the "
                "server: docker inspect vellur-ai-autopilot (OOMKilled, RestartCount), journalctl -k | grep -i oom."}),
    ("release:recovery-2026-11-16:incident", {
        "namespace": "Known Issues", "status": "proposed", "title": "Apartment task stopped twice after a controller restart",
        "tags": "incident,recovery", "paths": "docs/RECOVERY.md",
        "body": "Unconfirmed. Evidence in code: the message comes from a controller start; a manual 'continue' worked, so the "
                "runner kept the session, which points to a controller-only restart. Candidates not yet confirmed: release "
                "install, out-of-memory kill of the 256 MB controller (the conversation card fetched up to 5000 events "
                "every 3 s; fixed to fetch only new events), or a crash. Mark this record implemented with the cause once "
                "the server logs show it."}),
]
