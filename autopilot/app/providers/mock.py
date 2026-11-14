"""In-memory provider for automated tests and dry runs. Never touches the network."""
from __future__ import annotations

import asyncio
import itertools

from .base import BillingReport, GPUProvider, InstanceStatus, Offer, ProviderError


class MockProvider(GPUProvider):
    name = "mock"
    display = "Mock (tests)"
    paid = True

    def __init__(self, dph: float = 0.30, vram_gb: float = 32, fail: dict | None = None,
                 boot_polls: int = 1, delay: float = 0.0):
        self.dph, self.vram_gb, self.fail = dph, vram_gb, dict(fail or {})
        self.boot_polls, self.delay = boot_polls, delay
        self.instances: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.charges: list[dict] = []
        self._ids = itertools.count(1000)

    def configured(self):
        if self.fail.get("config"):
            return False, "missing credentials"
        return True, "ok"

    async def _maybe(self, op: str):
        self.calls.append((op,))
        if self.delay:
            await asyncio.sleep(self.delay)
        f = self.fail.get(op)
        if f == "timeout":
            raise asyncio.TimeoutError()
        if f:
            raise ProviderError(f, f"mock {op} failure: {f}")

    async def find_offer(self, profile, settings):
        await self._maybe("find_offer")
        if self.vram_gb < profile["min_vram_gb"]:
            raise ProviderError("capacity", "no offer with enough VRAM")
        return Offer("offer-1", "MOCK GPU", self.vram_gb, self.dph, extra_cost=0.05, location="NL")

    async def create(self, offer, profile, settings, label, ssh_public_key, vllm_api_key):
        await self._maybe("create")
        iid = str(next(self._ids))
        self.instances[iid] = {"state": "provisioning", "polls": 0, "label": label}
        return iid

    async def status(self, instance_id):
        await self._maybe("status")
        i = self.instances.get(instance_id)
        if not i:
            return InstanceStatus(instance_id, "terminated")
        if i["state"] == "provisioning":
            i["polls"] += 1
            if i["polls"] >= self.boot_polls:
                i["state"] = "running"
        return InstanceStatus(instance_id, i["state"], direct_url="http://mock-backend", dph=self.dph, label=i["label"])

    async def stop(self, instance_id, settings):
        await self._maybe("stop")
        if instance_id in self.instances and not self.fail.get("stop_ignored"):
            self.instances[instance_id]["state"] = "terminated"

    async def list_managed(self, label_prefix):
        await self._maybe("list")
        return [InstanceStatus(k, v["state"], label=v["label"]) for k, v in self.instances.items()
                if v["label"].startswith(label_prefix) and v["state"] != "terminated"]

    async def billing(self, month, month_start):
        await self._maybe("billing")
        return BillingReport(month, charges=list(self.charges), covered_until=None)
