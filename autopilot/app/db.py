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
CREATE TABLE IF NOT EXISTS routing_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, tag TEXT DEFAULT '', role TEXT NOT NULL, mode TEXT NOT NULL,
  policy TEXT NOT NULL, required TEXT DEFAULT '[]', chosen TEXT DEFAULT '', reason TEXT DEFAULT '',
  candidates TEXT DEFAULT '[]', est_usd REAL DEFAULT 0, outcome TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS routing_tag ON routing_decisions(tag);
CREATE TABLE IF NOT EXISTS timeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, project TEXT DEFAULT '', task_id INTEGER,
  conv_id INTEGER, correlation_id TEXT DEFAULT '', occurred_at REAL NOT NULL, observed_at REAL NOT NULL,
  source TEXT NOT NULL, event_type TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL,
  payload_version INTEGER DEFAULT 1, payload TEXT DEFAULT '{}', external_ref TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS timeline_task ON timeline(task_id, occurred_at);
CREATE INDEX IF NOT EXISTS timeline_project ON timeline(project, occurred_at);
CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT DEFAULT '', namespace TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, status TEXT DEFAULT 'implemented', source TEXT DEFAULT '', tags TEXT DEFAULT '',
  paths TEXT DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL, version INTEGER DEFAULT 1, locked INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS knowledge_project ON knowledge(project);
CREATE TABLE IF NOT EXISTS mission_objectives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active',           -- active | parked
  cost_estimate_usd REAL,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  objective_id INTEGER,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'backlog',          -- idea | backlog | ready | running | review | verified | parked
  plan TEXT NOT NULL DEFAULT '',
  blocked_reason TEXT NOT NULL DEFAULT '',
  repo TEXT NOT NULL DEFAULT '',
  base TEXT NOT NULL DEFAULT '',
  cost_estimate_usd REAL,
  depends_on INTEGER,
  agent_task_id INTEGER,
  position REAL NOT NULL DEFAULT 1000,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mission_items_obj ON mission_items(objective_id, position);
CREATE TABLE IF NOT EXISTS music_releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  artist TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned',          -- planned | released
  target_date TEXT NOT NULL DEFAULT '',            -- optional, artist-entered (YYYY-MM-DD or free text)
  released_at TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  cover_image_id TEXT NOT NULL DEFAULT '',         -- optional link to a real uploaded/generated image
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS music_tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  duration TEXT NOT NULL DEFAULT '',               -- free text, artist-entered
  notes TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS music_tracks_rel ON music_tracks(release_id, position);
CREATE TABLE IF NOT EXISTS music_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  artist_name TEXT NOT NULL DEFAULT '',
  genre TEXT NOT NULL DEFAULT '',
  bio TEXT NOT NULL DEFAULT '',
  links TEXT NOT NULL DEFAULT '',
  updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS studio_scenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  scene_json TEXT NOT NULL DEFAULT '{}',
  history_json TEXT NOT NULL DEFAULT '[]',
  version INTEGER NOT NULL DEFAULT 1,
  task_id INTEGER,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS studio_scenes_updated ON studio_scenes(updated DESC);
CREATE TABLE IF NOT EXISTS studio_snapshots (
  id TEXT PRIMARY KEY,
  scene_id INTEGER NOT NULL,
  mime TEXT NOT NULL DEFAULT 'image/png',
  width INTEGER NOT NULL DEFAULT 0,
  height INTEGER NOT NULL DEFAULT 0,
  size INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS studio_snapshots_scene ON studio_snapshots(scene_id, created DESC);
CREATE TABLE IF NOT EXISTS selfdev_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  goal TEXT NOT NULL DEFAULT '',
  stage TEXT NOT NULL DEFAULT 'INSPECT',
  target_paths TEXT NOT NULL DEFAULT '[]',
  inspection_json TEXT NOT NULL DEFAULT '{}',
  plan_text TEXT NOT NULL DEFAULT '',
  edits_json TEXT NOT NULL DEFAULT '[]',
  test_json TEXT NOT NULL DEFAULT '{}',
  preview_json TEXT NOT NULL DEFAULT '{}',
  review_json TEXT NOT NULL DEFAULT '{}',
  guardrails_json TEXT NOT NULL DEFAULT '{}',
  agent_task_id INTEGER,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS selfdev_runs_updated ON selfdev_runs(updated DESC);
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
        self._ro: dict = {}      # kv_get_shared: parsed values, dropped when the key is written
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

    def kv_get_shared(self, key: str, default: Any = None) -> Any:
        """Like kv_get, but parsed once and shared until the key is written again. For hot read paths with large
        values (model catalogs: hundreds of entries read for every model in a list). The result is shared:
        callers must NOT modify it."""
        with self._lock:
            hit = self._ro.get(key)
            if hit is None:
                r = self._c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
                hit = (json.loads(r["value"]),) if r else (None,)
                self._ro[key] = hit
        return default if hit[0] is None else hit[0]

    def kv_set(self, key: str, value: Any) -> None:
        with self._lock:
            self._ro.pop(key, None)
            self._c.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))
            self._ro.pop(key, None)

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
