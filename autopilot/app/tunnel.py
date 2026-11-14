"""SSH local forward from the controller to the GPU (vLLM on the GPU's 127.0.0.1:8000).

The VPS opens the connection with a dedicated key; the GPU side never receives a VPS credential.
Host keys: trust-on-first-use per host name (HyperAI's gateway keeps its key while the port changes), seeded from
the known_hosts file the owner already approved. A changed key for a known host aborts the connection.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import asyncssh

from .db import DB
from .providers.base import ProviderError

log = logging.getLogger("autopilot.tunnel")


def _seed_fingerprints(path: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3 or line.startswith("#"):
                    continue
                host = parts[0].split(",")[0]
                if host.startswith("["):
                    host = host[1:].split("]")[0]
                try:
                    fp = asyncssh.import_public_key(f"{parts[1]} {parts[2]}").get_fingerprint()
                except Exception:
                    continue
                out.setdefault(host, [])
                if fp not in out[host]:
                    out[host].append(fp)
    except FileNotFoundError:
        pass
    return out


class Tunnel:
    def __init__(self, db: DB, key_path: str, seed_known_hosts: str, local_port: int = 18000):
        self.db, self.key_path, self.seed, self.local_port = db, key_path, seed_known_hosts, local_port
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._listener = None
        self.target: Optional[tuple[str, int]] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.local_port}"

    def is_open(self) -> bool:
        return self._conn is not None and not self._conn.is_closed()

    def _trusted(self) -> dict[str, list[str]]:
        stored = self.db.kv_get("host_keys", None)
        if stored is None:
            stored = _seed_fingerprints(self.seed)
            self.db.kv_set("host_keys", stored)
        return stored

    async def open(self, host: str, port: int, user: str = "root", remote_port: int = 8000,
                   timeout: float = 25.0) -> None:
        await self.close()
        if not os.path.exists(self.key_path):
            raise ProviderError("config", "GPU SSH key missing on the server (sudo vac gpu init)")
        try:
            conn = await asyncio.wait_for(asyncssh.connect(
                host, port=port, username=user, client_keys=[self.key_path], known_hosts=None,
                keepalive_interval=15, keepalive_count_max=3, agent_path=None), timeout)
        except asyncio.TimeoutError:
            raise ProviderError("timeout", f"SSH to {host}:{port} timed out")
        except (OSError, asyncssh.Error) as e:
            raise ProviderError("provider", f"SSH to {host}:{port} failed: {e.__class__.__name__}: {e}")
        fp = conn.get_server_host_key().get_fingerprint()
        trusted = self._trusted()
        known = trusted.get(host)
        if known and fp not in known:
            conn.close()
            self.db.event("alert", "security", f"SSH host key for {host} changed; connection refused", fingerprint=fp)
            raise ProviderError("auth", f"SSH host key for {host} changed (possible interception); refused")
        if not known:
            trusted[host] = [fp]
            self.db.kv_set("host_keys", trusted)
            self.db.event("info", "security", f"Trusted new SSH host key for {host} (first use)", fingerprint=fp)
        try:
            self._listener = await conn.forward_local_port("127.0.0.1", self.local_port, "127.0.0.1", remote_port)
        except (OSError, asyncssh.Error) as e:
            conn.close()
            raise ProviderError("provider", f"port forward failed: {e}")
        self._conn, self.target = conn, (host, port)
        log.info("tunnel open to %s:%s", host, port)

    async def close(self) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._conn is not None:
            self._conn.close()
            try:
                await asyncio.wait_for(self._conn.wait_closed(), 5)
            except Exception:
                pass
            self._conn = None
        self.target = None
