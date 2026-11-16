"""Controller restart diagnostics.

Every 15 s the controller writes a heartbeat with its memory use (process RSS and, inside a container, the cgroup's
memory.current / memory.max / memory.peak and the oom / oom_kill counters). A normal shutdown (SIGTERM from Docker,
`vac restart`, a release install) writes a shutdown record. At the next start the two are compared:

  shutdown record present         → clean stop (deploy, restart, docker stop)
  no shutdown record              → the process was killed without cleanup: SIGKILL, out-of-memory kill or a crash of
                                    the host; the last heartbeat shows the memory use just before
  installed release changed       → the restart came with a release install

The result is stored as a boot record (last 30) and as an event, so restarts can be correlated with task failures.
"""
from __future__ import annotations

import asyncio
import os
import time

CG = "/sys/fs/cgroup"


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def memory() -> dict:
    out: dict = {}
    for line in _read("/proc/self/status").splitlines():
        if line.startswith("VmRSS:"):
            out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
    for name in ("memory.current", "memory.max", "memory.peak"):
        v = _read(os.path.join(CG, name))
        if v.isdigit():
            out[name.split(".")[1] + "_mb"] = round(int(v) / 1048576, 1)
        elif v:
            out[name.split(".")[1]] = v
    for line in _read(os.path.join(CG, "memory.events")).splitlines():
        k, _, v = line.partition(" ")
        if k in ("oom", "oom_kill", "max") and v.isdigit():
            out["events_" + k] = int(v)
    return out


class Health:
    def __init__(self, db, release_state=None, clock=time.time):
        self.db, self.release_state, self.clock = db, release_state or (lambda: {}), clock

    def boot(self) -> dict:
        now = self.clock()
        hb = self.db.kv_get("controller_heartbeat") or {}
        down = self.db.kv_get("controller_shutdown") or {}
        rel = self.release_state() or {}
        prev_installed = (self.db.kv_get("controller_boots") or [{}])[-1].get("installed", "") if self.db.kv_get("controller_boots") else ""
        rec = {"ts": now, "memory": memory(), "installed": rel.get("installed", ""), "release_last": rel.get("last", "")}
        if not hb:
            rec.update(kind="first", summary="controller started (no earlier heartbeat recorded)")
        elif down and down.get("ts", 0) >= hb.get("ts", 0) - 1:
            rec.update(kind="clean", summary=f"controller started after a clean shutdown ({down.get('reason', 'signal')}) "
                                             f"{int(now - down['ts'])} s earlier")
        else:
            m = hb.get("memory") or {}
            mem = ", ".join(f"{k}={v}" for k, v in m.items())
            rec.update(kind="unclean", last_heartbeat=hb.get("ts"), last_memory=m,
                       summary=f"controller started after an UNCLEAN stop: no shutdown record; last heartbeat "
                               f"{int(now - hb.get('ts', now))} s earlier ({mem or 'memory unknown'}). A SIGKILL, an "
                               f"out-of-memory kill or a host crash leaves no shutdown record.")
        if prev_installed and rec["installed"] and rec["installed"] != prev_installed:
            rec["summary"] += f" The installed release changed {prev_installed[:7]} → {rec['installed'][:7]}."
            rec["release_changed"] = True
        boots = (self.db.kv_get("controller_boots") or [])[-29:] + [rec]
        self.db.kv_set("controller_boots", boots)
        self.db.kv_set("controller_shutdown", {})
        self.db.event("warn" if rec["kind"] == "unclean" else "info", "boot", rec["summary"],
                      stop=rec["kind"], installed=rec["installed"])
        self.beat()
        return rec

    def beat(self) -> None:
        self.db.kv_set("controller_heartbeat", {"ts": self.clock(), "memory": memory()})

    def shutdown(self, reason: str = "signal") -> None:
        self.db.kv_set("controller_shutdown", {"ts": self.clock(), "reason": reason})

    async def run(self, every: float = 15.0) -> None:
        while True:
            await asyncio.sleep(every)
            self.beat()

    def report(self) -> dict:
        return {"boots": list(reversed(self.db.kv_get("controller_boots") or [])),
                "heartbeat": self.db.kv_get("controller_heartbeat") or {}, "now": {"ts": self.clock(), "memory": memory()}}
