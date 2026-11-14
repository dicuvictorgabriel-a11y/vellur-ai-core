"""Administrator notifications: always recorded as audit events; optionally POSTed to a webhook
(AUTOPILOT_NOTIFY_URL, e.g. a self-hosted ntfy topic). Failures never affect the controller."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("autopilot.notify")


class Notifier:
    def __init__(self, db, url: str, client: httpx.AsyncClient | None = None):
        self.db, self.url = db, url
        self._client = client

    async def alert(self, kind: str, message: str, **data) -> None:
        self.db.event("alert", kind, message, **data)
        if not self.url:
            return
        try:
            c = self._client or httpx.AsyncClient(timeout=10)
            await c.post(self.url, content=f"VELLUR AI Autopilot: {message}".encode(),
                         headers={"Title": "VELLUR AI Autopilot", "Tags": kind})
            if self._client is None:
                await c.aclose()
        except Exception as e:  # pragma: no cover - best effort
            log.warning("notify failed: %s", e)
