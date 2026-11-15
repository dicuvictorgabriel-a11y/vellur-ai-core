"""Persistent state in SQLite (single file in the data volume). Small, synchronous, guarded by a lock."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL, profile TEXT NOT NULL, trigger TEXT NOT NULL,
  state TEXT NOT NULL, reason TEXT DEFAULT '',
  instance_id TEXT DEFAULT '', offer TEXT DEFAULT '{}',
  gpu_name TEXT DEFAULT '', vram_gb REAL DEFAULT 0, dph REAL DEFAULT 0, extra_cost REAL DEFAULT 0,
  created_at REAL NOT NULL, ready_at REAL, billing_started_at REAL, ended_at REAL,
  termination_verified INTEGER DEFAULT 0, confirmed_cost REAL, reconciled INTEGER DEFAULT 0,
  dry_run INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, month TEXT NOT NULL,
  provider TEXT NOT NULL, kind TEXT NOT NULL, amount_usd REAL NOT NULL, ref TEXT DEFAULT '', note TEXT DEFAULT '',
  UNIQUE(provider, kind, ref)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, level TEXT NOT NULL,
  kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS alerts (month TEXT NOT NULL, key TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY(month, key));
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, month TEXT NOT NULL, role TEXT NOT NULL,
  target TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, ok INTEGER NOT NULL, status INTEGER DEFAULT 0,
  in_tokens INTEGER DEFAULT 0, out_tokens INTEGER DEFAULT 0, cached_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0, cost_source TEXT DEFAULT '', latency_ms INTEGER DEFAULT 0, tag TEXT DEFAULT '',
  error TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS usage_month ON usage(month);
CREATE INDEX IF NOT EXISTS usage_tag ON usage(tag);
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, model TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  created REAL NOT NULL, meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv_id);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, updated REAL NOT NULL, state TEXT NOT NULL,
  repo TEXT DEFAULT '', base TEXT DEFAULT '', branch TEXT DEFAULT '', model TEXT NOT NULL, prompt TEXT NOT NULL,
  error TEXT DEFAULT '', files_changed INTEGER DEFAULT 0, pr_url TEXT DEFAULT '', started REAL, ended REAL
);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, ts REAL NOT NULL, run INTEGER DEFAULT 1,
  kind TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS task_events_task ON task_events(task_id, id);
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, mime TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT NOT NULL, created REAL NOT NULL, conv_id INTEGER, task_id INTEGER,
  extracted TEXT DEFAULT '', meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS attachments_conv ON attachments(conv_id);
CREATE INDEX IF NOT EXISTS attachments_task ON attachments(task_id);
CREATE TABLE IF NOT EXISTS generated_images (
  id TEXT PRIMARY KEY, created REAL NOT NULL, prompt TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  target TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, width INTEGER DEFAULT 0, height INTEGER DEFAULT 0,
  sha256 TEXT NOT NULL, cost_usd REAL DEFAULT 0, cost_source TEXT DEFAULT '', conv_id INTEGER, task_id INTEGER,
  meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS generated_images_conv ON generated_images(conv_id);
CREATE INDEX IF NOT EXISTS generated_images_task ON generated_images(task_id);
"""

# Columns added after the first release (ALTER TABLE ... ADD COLUMN is skipped when the column exists).
MIGRATIONS = [
    ("tasks", "outcome", "TEXT DEFAULT ''"),       # completed | partial | blocked | unverified | incomplete | needs_attention | failed | stopped | cancelled
    ("tasks", "runs", "INTEGER DEFAULT 1"),        # 1 + number of follow-up messages
    ("tasks", "cursor", "INTEGER DEFAULT 0"),      # runner event lines already imported
    ("tasks", "report", "TEXT DEFAULT '{}'"),      # final report of the latest run (JSON)
    ("tasks", "base_sha", "TEXT DEFAULT ''"),      # commit the workspace started from (or was last published as)
    ("usage", "images", "INTEGER DEFAULT 0"),       # generated images (image models have no text tokens)
]


class DB:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA synchronous=NORMAL")
        self._c.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            cols = {r[1] for r in self._c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self._c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._c.execute(sql, args)

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._c.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        with self._lock:
            r = self._c.execute(sql, args).fetchone()
            return dict(r) if r else None

    # key/value helpers
    def kv_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def kv_set(self, key: str, value: Any) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

    def settings_get(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.all("SELECT key, value FROM settings")}

    def settings_put(self, values: dict) -> None:
        for k, v in values.items():
            self.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (k, json.dumps(v)))

    def event(self, level: str, kind: str, message: str, **data: Any) -> dict:
        ts = time.time()
        cur = self.x("INSERT INTO events(ts,level,kind,message,data) VALUES(?,?,?,?,?)",
                     (ts, level, kind, message, json.dumps(data, default=str)))
        return {"id": cur.lastrowid, "ts": ts, "level": level, "kind": kind, "message": message, "data": data}

    def events(self, limit: int = 100) -> list[dict]:
        rows = self.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = json.loads(r["data"] or "{}")
        return rows

    def alert_once(self, month: str, key: str) -> bool:
        """True the first time (month, key) is seen."""
        try:
            self.x("INSERT INTO alerts(month,key,ts) VALUES(?,?,?)", (month, key, time.time()))
            return True
        except sqlite3.IntegrityError:
            return False
