"""NOVA Context Bridge: reusable context layer for multiple agents.

One operational source of truth. One reusable context layer. Multiple intelligent agents.

Authoritative sources (read-only for context):
- timeline: requests, runs, file changes, checks, routing, PRs, merges, deployments, failures, recoveries
- knowledge: durable project memory (FTS5 searchable)
- agent tasks: runs, events, outcomes, reports
- controller: GPU sessions, profiles, providers, health, restarts
- releases: version, delivery record, GitHub/installer report
- health: controller boots, heartbeats, memory, OOM
- screenshots: metadata only (no file access)

Context items are derived, not authoritative. They are refreshed automatically and on demand.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from .db import DB


CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    source TEXT NOT NULL,           -- 'timeline', 'knowledge', 'agent', 'controller', 'release', 'health', 'screenshot'
    type TEXT NOT NULL,             -- 'task', 'run', 'event', 'decision', 'deployment', 'failure', 'recovery', 'knowledge_entry', 'session', 'boot', 'heartbeat', 'release', 'screenshot_meta'
    ref_id TEXT,                    -- stable ID from authoritative source (task_id, run_id, event_id, knowledge_id, session_id, release_id)
    title TEXT NOT NULL,
    summary TEXT,                   -- concise summary for agent injection
    content TEXT,                   -- full content (JSON or text)
    metadata TEXT,                  -- JSON: additional structured data (cost, tokens, files, urls, etc.)
    tags TEXT,                      -- comma-separated tags for filtering
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL,                -- optional TTL for ephemeral context
    UNIQUE(project, source, type, ref_id)
);

CREATE INDEX IF NOT EXISTS idx_context_project_source ON context_items(project, source);
CREATE INDEX IF NOT EXISTS idx_context_project_type ON context_items(project, type);
CREATE INDEX IF NOT EXISTS idx_context_project_created ON context_items(project, created_at);
CREATE INDEX IF NOT EXISTS idx_context_expires ON context_items(expires_at);

-- FTS5 index for full-text search (falls back to LIKE if unavailable)
CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(
    project, source, type, title, summary, content, tags,
    content='context_items', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS context_fts_ai AFTER INSERT ON context_items BEGIN
    INSERT INTO context_fts(rowid, project, source, type, title, summary, content, tags)
    VALUES (new.id, new.project, new.source, new.type, new.title, new.summary, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS context_fts_ad AFTER DELETE ON context_items BEGIN
    INSERT INTO context_fts(context_fts, rowid, project, source, type, title, summary, content, tags)
    VALUES ('delete', old.id, old.project, old.source, old.type, old.title, old.summary, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS context_fts_au AFTER UPDATE ON context_items BEGIN
    INSERT INTO context_fts(context_fts, rowid, project, source, type, title, summary, content, tags)
    VALUES ('delete', old.id, old.project, old.source, old.type, old.title, old.summary, old.content, old.tags);
    INSERT INTO context_fts(rowid, project, source, type, title, summary, content, tags)
    VALUES (new.id, new.project, new.source, new.type, new.title, new.summary, new.content, new.tags);
END;

-- Refresh log for audit and debugging
CREATE TABLE IF NOT EXISTS context_refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    source TEXT NOT NULL,
    items_added INTEGER DEFAULT 0,
    items_updated INTEGER DEFAULT 0,
    items_removed INTEGER DEFAULT 0,
    duration_ms INTEGER,
    error TEXT,
    triggered_by TEXT,              -- 'auto', 'manual', 'mcp', 'restart'
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_refresh_log_project ON context_refresh_log(project, created_at);

-- MCP access tokens (hashed, write-once per client)
CREATE TABLE IF NOT EXISTS mcp_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL,
    scopes TEXT NOT NULL,           -- comma-separated: 'read:context', 'read:knowledge', 'read:timeline', 'read:agent', 'read:controller', 'read:release', 'read:health', 'read:screenshot'
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL
);

CREATE INDEX IF NOT EXISTS idx_mcp_tokens_client ON mcp_tokens(client_name);
"""


@dataclass
class ContextItem:
    id: Optional[int]
    project: str
    source: str
    type: str
    ref_id: Optional[str]
    title: str
    summary: Optional[str]
    content: Optional[str]
    metadata: Optional[str]
    tags: Optional[str]
    created_at: float
    updated_at: float
    expires_at: Optional[float]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project": self.project,
            "source": self.source,
            "type": self.type,
            "ref_id": self.ref_id,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "metadata": json.loads(self.metadata) if self.metadata else None,
            "tags": self.tags.split(",") if self.tags else [],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }


@dataclass
class RefreshResult:
    project: str
    source: str
    items_added: int
    items_updated: int
    items_removed: int
    duration_ms: int
    error: Optional[str]
    triggered_by: str


class ContextService:
    """Reusable context service: derives context from authoritative sources, serves via MCP and API."""

    def __init__(self, db: DB):
        self.db = db
        self._lock = threading.RLock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            # Split by semicolon but respect triggers (BEGIN...END blocks)
            stmts = []
            current = []
            depth = 0
            for line in CONTEXT_SCHEMA.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                current.append(line)
                # Track BEGIN/END for triggers
                if "BEGIN" in stripped.upper() and "END" not in stripped.upper():
                    depth += 1
                if "END" in stripped.upper() and ";" in stripped:
                    depth -= 1
                if stripped.endswith(";") and depth == 0:
                    stmts.append("\n".join(current))
                    current = []
            if current:
                stmts.append("\n".join(current))
            for stmt in stmts:
                stmt = stmt.strip()
                if stmt:
                    self.db.x(stmt)

    def _execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.db.x(sql, args)

    # ---- Core CRUD ----

    def upsert(self, item: ContextItem) -> int:
        now = time.time()
        if item.id is None:
            cur = self._execute(
                """INSERT INTO context_items
                   (project, source, type, ref_id, title, summary, content, metadata, tags, created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item.project, item.source, item.type, item.ref_id, item.title,
                 item.summary, item.content, item.metadata, item.tags, now, now, item.expires_at),
            )
            item.id = cur.lastrowid
        else:
            self._execute(
                """UPDATE context_items SET
                   title=?, summary=?, content=?, metadata=?, tags=?, updated_at=?, expires_at=?
                   WHERE id=?""",
                (item.title, item.summary, item.content, item.metadata, item.tags, now, item.expires_at, item.id),
            )
        return item.id

    def get(self, project: str, source: str, type: str, ref_id: str) -> Optional[ContextItem]:
        row = self.db.one(
            "SELECT * FROM context_items WHERE project=? AND source=? AND type=? AND ref_id=?",
            (project, source, type, ref_id),
        )
        return self._row_to_item(row) if row else None

    def delete(self, project: str, source: str, type: str, ref_id: str) -> bool:
        cur = self._execute(
            "DELETE FROM context_items WHERE project=? AND source=? AND type=? AND ref_id=?",
            (project, source, type, ref_id),
        )
        return cur.rowcount > 0

    def query(
        self,
        project: str,
        source: Optional[str] = None,
        type: Optional[str] = None,
        tags: Optional[list[str]] = None,
        limit: int = 100,
        offset: int = 0,
        since: Optional[float] = None,
        until: Optional[float] = None,
        include_expired: bool = False,
    ) -> list[ContextItem]:
        sql = "SELECT * FROM context_items WHERE project=?"
        params: list[Any] = [project]
        if source:
            sql += " AND source=?"
            params.append(source)
        if type:
            sql += " AND type=?"
            params.append(type)
        if tags:
            for tag in tags:
                sql += " AND tags LIKE ?"
                params.append(f"%{tag}%")
        if not include_expired:
            sql += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(time.time())
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        if until:
            sql += " AND created_at <= ?"
            params.append(until)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.db.all(sql, tuple(params))
        return [self._row_to_item(r) for r in rows]

    def search(self, project: str, query: str, limit: int = 50) -> list[ContextItem]:
        """Full-text search using FTS5, falls back to LIKE."""
        try:
            rows = self.db.all(
                """SELECT c.* FROM context_items c
                   JOIN context_fts f ON c.id = f.rowid
                   WHERE c.project=? AND context_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (project, query, limit),
            )
            if rows:
                return [self._row_to_item(r) for r in rows]
        except sqlite3.OperationalError:
            pass
        # Fallback to LIKE
        like_query = f"%{query}%"
        rows = self.db.all(
            """SELECT * FROM context_items
               WHERE project=? AND (title LIKE ? OR summary LIKE ? OR content LIKE ? OR tags LIKE ?)
               ORDER BY created_at DESC LIMIT ?""",
            (project, like_query, like_query, like_query, like_query, limit),
        )
        return [self._row_to_item(r) for r in rows]

    def _row_to_item(self, row: dict) -> ContextItem:
        return ContextItem(
            id=row.get("id"),
            project=row["project"],
            source=row["source"],
            type=row["type"],
            ref_id=row.get("ref_id"),
            title=row["title"],
            summary=row.get("summary"),
            content=row.get("content"),
            metadata=row.get("metadata"),
            tags=row.get("tags"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row.get("expires_at"),
        )

    # ---- Refresh from authoritative sources ----

    def refresh_all(self, project: str, triggered_by: str = "auto") -> list[RefreshResult]:
        """Refresh context from all authoritative sources."""
        results = []
        for source in ("timeline", "knowledge", "agent", "controller", "health", "screenshot"):
            results.append(self.refresh_source(project, source, triggered_by))
        return results

    def refresh_source(self, project: str, source: str, triggered_by: str = "auto") -> RefreshResult:
        """Refresh context from a single authoritative source."""
        start = time.time()
        added = updated = removed = 0
        error = None
        try:
            if source == "timeline":
                added, updated = self._refresh_timeline(project)
            elif source == "knowledge":
                added, updated = self._refresh_knowledge(project)
            elif source == "agent":
                added, updated = self._refresh_agent(project)
            elif source == "controller":
                added, updated = self._refresh_controller(project)
            elif source == "release":
                added, updated = self._refresh_release(project)
            elif source == "health":
                added, updated = self._refresh_health(project)
            elif source == "screenshot":
                added, updated = self._refresh_screenshot(project)
            else:
                error = f"unknown source: {source}"
        except Exception as e:
            error = str(e)
        duration_ms = int((time.time() - start) * 1000)
        self._log_refresh(project, source, added, updated, removed, duration_ms, error, triggered_by)
        return RefreshResult(project, source, added, updated, removed, duration_ms, error, triggered_by)

    def _log_refresh(
        self, project: str, source: str, added: int, updated: int, removed: int,
        duration_ms: int, error: Optional[str], triggered_by: str
    ) -> None:
        self._execute(
            """INSERT INTO context_refresh_log
               (project, source, items_added, items_updated, items_removed, duration_ms, error, triggered_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project, source, added, updated, removed, duration_ms, error, triggered_by, time.time()),
        )

    # ---- Source-specific refresh implementations ----

    def _refresh_timeline(self, project: str) -> tuple[int, int]:
        """Refresh from timeline: tasks, runs, file changes, checks, routing, PRs, merges, deployments, failures, recoveries."""
        added = updated = 0
        events = self.db.all(
            """SELECT * FROM timeline WHERE project=? ORDER BY occurred_at DESC LIMIT 5000""",
            (project,),
        )
        for ev in events:
            item = ContextItem(
                id=None,
                project=project,
                source="timeline",
                type=ev["event_type"],
                ref_id=str(ev["id"]),
                title=f"{ev['event_type']}: {ev.get('title', '')}",
                summary=ev.get("title"),
                content=json.dumps(dict(ev)),
                metadata=json.dumps({"correlation_id": ev.get("correlation_id"), "source": ev.get("source")}),
                tags=ev.get("category"),
                created_at=ev["occurred_at"],
                updated_at=ev.get("observed_at", ev["occurred_at"]),
                expires_at=None,
            )
            existing = self.get(project, "timeline", ev["event_type"], str(ev["id"]))
            if existing:
                item.id = existing.id
                self.upsert(item)
                updated += 1
            else:
                self.upsert(item)
                added += 1
        return added, updated

    def _refresh_knowledge(self, project: str) -> tuple[int, int]:
        """Refresh from knowledge table."""
        added = updated = 0
        entries = self.db.all(
            "SELECT * FROM knowledge WHERE project=? ORDER BY updated DESC LIMIT 1000",
            (project,),
        )
        for entry in entries:
            item = ContextItem(
                id=None,
                project=project,
                source="knowledge",
                type="knowledge_entry",
                ref_id=str(entry["id"]),
                title=entry["title"],
                summary=entry.get("body")[:200] if entry.get("body") else None,
                content=entry.get("body"),
                metadata=json.dumps({"tags": entry.get("tags"), "source": entry.get("source"), "namespace": entry.get("namespace")}),
                tags=entry.get("tags"),
                created_at=entry["created"],
                updated_at=entry["updated"],
                expires_at=None,
            )
            existing = self.get(project, "knowledge", "knowledge_entry", str(entry["id"]))
            if existing:
                item.id = existing.id
                self.upsert(item)
                updated += 1
            else:
                self.upsert(item)
                added += 1
        return added, updated

    def _refresh_agent(self, project: str) -> tuple[int, int]:
        """Refresh from agent tasks: tasks, runs (from runs column), events, outcomes."""
        added = updated = 0
        tasks = self.db.all(
            "SELECT * FROM tasks WHERE repo=? ORDER BY created DESC LIMIT 200",
            (project,),
        )
        for task in tasks:
            task_item = ContextItem(
                id=None,
                project=project,
                source="agent",
                type="task",
                ref_id=str(task["id"]),
                title=f"Task: {task.get('prompt', '')[:50]}...",
                summary=task.get("outcome"),
                content=json.dumps(dict(task)),
                metadata=json.dumps({"state": task.get("state"), "model": task.get("model"), "runs": task.get("runs"), "repo": task.get("repo")}),
                tags=f"task,{task.get('state', '')}",
                created_at=task["created"],
                updated_at=task["updated"],
                expires_at=None,
            )
            existing = self.get(project, "agent", "task", str(task["id"]))
            if existing:
                task_item.id = existing.id
                self.upsert(task_item)
                updated += 1
            else:
                self.upsert(task_item)
                added += 1

            # Get run events from task_events for this task
            runs = task.get("runs", 1)
            for run_num in range(1, runs + 1):
                events = self.db.all(
                    "SELECT * FROM task_events WHERE task_id=? AND run=? ORDER BY ts",
                    (task["id"], run_num),
                )
                if events:
                    # Build a run summary from events
                    run_content = json.dumps([dict(e) for e in events])
                    outcome = "running"
                    for e in reversed(events):
                        if e["kind"] in ("done", "error", "completed"):
                            outcome = e["kind"]
                            break
                    run_item = ContextItem(
                        id=None,
                        project=project,
                        source="agent",
                        type="run",
                        ref_id=f"{task['id']}:{run_num}",
                        title=f"Run {run_num} of task {task['id']}",
                        summary=outcome,
                        content=run_content,
                        metadata=json.dumps({"outcome": outcome, "event_count": len(events)}),
                        tags=f"run,{outcome}",
                        created_at=events[0]["ts"] if events else task["created"],
                        updated_at=events[-1]["ts"] if events else task["updated"],
                        expires_at=None,
                    )
                    existing = self.get(project, "agent", "run", f"{task['id']}:{run_num}")
                    if existing:
                        run_item.id = existing.id
                        self.upsert(run_item)
                        updated += 1
                    else:
                        self.upsert(run_item)
                        added += 1
        return added, updated

    def _refresh_controller(self, project: str) -> tuple[int, int]:
        """Refresh from controller: GPU sessions, profiles, providers."""
        added = updated = 0
        # sessions table doesn't have project column; get all recent sessions
        sessions = self.db.all(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT 50",
        )
        for session in sessions:
            item = ContextItem(
                id=None,
                project=project,
                source="controller",
                type="session",
                ref_id=str(session["id"]),
                title=f"GPU Session: {session.get('profile', 'unknown')}",
                summary=session.get("state"),
                content=json.dumps(dict(session)),
                metadata=json.dumps({"profile": session.get("profile"), "provider": session.get("provider"), "gpu": session.get("gpu_name"), "vram_gb": session.get("vram_gb")}),
                tags=f"session,{session.get('state', '')}",
                created_at=session["created_at"],
                updated_at=session.get("ready_at", session["created_at"]),
                expires_at=None,
            )
            existing = self.get(project, "controller", "session", str(session["id"]))
            if existing:
                item.id = existing.id
                self.upsert(item)
                updated += 1
            else:
                self.upsert(item)
                added += 1
        return added, updated

    def _refresh_release(self, project: str) -> tuple[int, int]:
        """Refresh from releases: version, delivery record, GitHub/installer report.
        Note: releases are tracked via timeline events and GitHub, not a local table.
        This method returns (0, 0) as a placeholder."""
        return 0, 0

    def _refresh_health(self, project: str) -> tuple[int, int]:
        """Refresh from health: controller boots, heartbeats, memory, OOM."""
        added = updated = 0
        boots = self.db.kv_get("controller_boots") or []
        for i, boot in enumerate(boots):
            item = ContextItem(
                id=None,
                project=project,
                source="health",
                type="boot",
                ref_id=f"boot_{i}",
                title=f"Controller Boot #{len(boots) - i}",
                summary=boot.get("clean") and "clean" or "unclean",
                content=json.dumps(boot),
                metadata=json.dumps({"memory": boot.get("memory"), "clean": boot.get("clean")}),
                tags="boot,health",
                created_at=boot.get("ts", time.time()),
                updated_at=boot.get("ts", time.time()),
                expires_at=None,
            )
            existing = self.get(project, "health", "boot", f"boot_{i}")
            if existing:
                item.id = existing.id
                self.upsert(item)
                updated += 1
            else:
                self.upsert(item)
                added += 1

        heartbeat = self.db.kv_get("controller_heartbeat") or {}
        if heartbeat:
            item = ContextItem(
                id=None,
                project=project,
                source="health",
                type="heartbeat",
                ref_id="latest",
                title="Controller Heartbeat",
                summary=f"RSS: {heartbeat.get('memory', {}).get('rss_mb', '?')} MB",
                content=json.dumps(heartbeat),
                metadata=json.dumps(heartbeat.get("memory", {})),
                tags="heartbeat,health",
                created_at=heartbeat.get("ts", time.time()),
                updated_at=heartbeat.get("ts", time.time()),
                expires_at=heartbeat.get("ts", time.time()) + 300,
            )
            existing = self.get(project, "health", "heartbeat", "latest")
            if existing:
                item.id = existing.id
                self.upsert(item)
                updated += 1
            else:
                self.upsert(item)
                added += 1
        return added, updated

    def _refresh_screenshot(self, project: str) -> tuple[int, int]:
        """Refresh screenshot metadata (no file access, only metadata)."""
        added = updated = 0
        # task_events doesn't have project column, join with tasks (uses repo column)
        shots = self.db.all(
            """SELECT te.* FROM task_events te
               JOIN tasks t ON te.task_id = t.id
               WHERE t.repo=? AND te.kind='shot' ORDER BY te.ts DESC LIMIT 100""",
            (project,),
        )
        for shot in shots:
            data = shot.get("data", {})
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}
            item = ContextItem(
                id=None,
                project=project,
                source="screenshot",
                type="screenshot_meta",
                ref_id=str(shot["id"]),
                title=f"Screenshot: {data.get('name', 'unnamed')}",
                summary=data.get("description"),
                content=json.dumps({"url": data.get("url"), "viewport": data.get("viewport"), "task_id": shot.get("task_id"), "run": shot.get("run")}),
                metadata=json.dumps({"task_id": shot.get("task_id"), "run": shot.get("run"), "viewport": data.get("viewport")}),
                tags=f"screenshot,{data.get('viewport', '')}",
                created_at=shot["ts"],
                updated_at=shot["ts"],
                expires_at=None,
            )
            existing = self.get(project, "screenshot", "screenshot_meta", str(shot["id"]))
            if existing:
                item.id = existing.id
                self.upsert(item)
                updated += 1
            else:
                self.upsert(item)
                added += 1
        return added, updated

    # ---- Agent context injection ----

    def get_agent_context(
        self,
        project: str,
        task_id: Optional[str] = None,
        max_items: int = 50,
        max_tokens: int = 8000,
    ) -> dict:
        """Get bounded, relevant context for agent injection. No paid inference."""
        context = {
            "project": project,
            "task_id": task_id,
            "knowledge": [],
            "recent_tasks": [],
            "recent_runs": [],
            "recent_decisions": [],
            "active_sessions": [],
            "latest_release": None,
            "health_status": "unknown",
            "screenshots": [],
            "budget": {"max_items": max_items, "max_tokens": max_tokens},
        }

        knowledge = self.query(project, source="knowledge", type="knowledge_entry", limit=10)
        context["knowledge"] = [k.to_dict() for k in knowledge]

        tasks = self.query(project, source="agent", type="task", limit=5)
        context["recent_tasks"] = [t.to_dict() for t in tasks]

        if task_id:
            runs = self.query(project, source="agent", type="run", limit=5)
            runs = [r for r in runs if r.ref_id and r.ref_id.startswith(f"{task_id}:")]
        else:
            runs = self.query(project, source="agent", type="run", limit=5)
        context["recent_runs"] = [r.to_dict() for r in runs]

        decisions = self.query(
            project, source="timeline",
            type=None,
            limit=20,
        )
        decision_types = {"routing", "deployment", "failure", "recovery", "check", "pr", "merge"}
        context["recent_decisions"] = [
            d.to_dict() for d in decisions if d.type in decision_types
        ][:10]

        sessions = self.query(project, source="controller", type="session", limit=5)
        active = [s for s in sessions if s.summary in ("ready", "loading_model", "starting", "provisioning", "searching", "attaching")]
        context["active_sessions"] = [s.to_dict() for s in active]

        health = self.query(project, source="health", type="heartbeat", limit=1)
        if health:
            context["health_status"] = "healthy" if "clean" in (health[0].summary or "") else "degraded"
        else:
            boots = self.query(project, source="health", type="boot", limit=1)
            if boots:
                context["health_status"] = "recently_restarted"

        shots = self.query(project, source="screenshot", type="screenshot_meta", limit=5)
        context["screenshots"] = [s.to_dict() for s in shots]

        return context

    # ---- MCP Token Management ----

    def create_mcp_token(self, client_name: str, scopes: list[str]) -> str:
        """Create a new MCP token. Returns the plaintext token (only shown once)."""
        import secrets
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        self._execute(
            """INSERT INTO mcp_tokens (client_name, token_hash, scopes, created_at)
               VALUES (?, ?, ?, ?)""",
            (client_name, token_hash, ",".join(scopes), time.time()),
        )
        return token

    def verify_mcp_token(self, token: str) -> Optional[dict]:
        """Verify MCP token and return client info if valid."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.one(
            "SELECT * FROM mcp_tokens WHERE token_hash=? AND revoked_at IS NULL",
            (token_hash,),
        )
        if row:
            self._execute(
                "UPDATE mcp_tokens SET last_used_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            return {
                "client_name": row["client_name"],
                "scopes": row["scopes"].split(",") if row["scopes"] else [],
            }
        return None

    def revoke_mcp_token(self, client_name: str) -> bool:
        cur = self._execute(
            "UPDATE mcp_tokens SET revoked_at=? WHERE client_name=?",
            (time.time(), client_name),
        )
        return cur.rowcount > 0

    def list_mcp_tokens(self) -> list[dict]:
        rows = self.db.all("SELECT client_name, scopes, created_at, last_used_at, revoked_at FROM mcp_tokens")
        return [dict(r) for r in rows]

    # ---- Restart Recovery ----

    def on_controller_start(self, project: str) -> dict:
        """Called on controller start to recover context and mark restart."""
        results = self.refresh_all(project, triggered_by="restart")
        self.upsert(ContextItem(
            id=None,
            project=project,
            source="health",
            type="restart_recovery",
            ref_id=f"restart_{int(time.time())}",
            title="Controller Restart - Context Recovered",
            summary=f"Refreshed {len(results)} sources",
            content=json.dumps([r.__dict__ for r in results]),
            metadata=json.dumps({"sources": [r.source for r in results]}),
            tags="restart,recovery",
            created_at=time.time(),
            updated_at=time.time(),
            expires_at=None,
        ))
        return {"project": project, "refresh_results": [r.__dict__ for r in results]}


_context_service: Optional[ContextService] = None


def get_context_service(db: DB) -> ContextService:
    global _context_service
    if _context_service is None:
        _context_service = ContextService(db)
    return _context_service