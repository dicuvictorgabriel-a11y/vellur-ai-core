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
"""


class DB:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA synchronous=NORMAL")
        self._c.executescript(SCHEMA)

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
