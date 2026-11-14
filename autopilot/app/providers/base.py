"""Provider abstraction. Add RunPod, dedicated servers or local GPUs by implementing GPUProvider."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class ProviderError(Exception):
    """code: capacity | budget | auth | config | provider | timeout | manual | dry_run"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Offer:
    offer_id: str
    gpu_name: str
    vram_gb: float
    dph: float                  # USD per hour as quoted by the provider (compute + quoted storage)
    extra_cost: float = 0.0     # one-off estimate (e.g. model download traffic)
    location: str = ""
    reliability: Optional[float] = None
    raw: dict = field(default_factory=dict)


@dataclass
class InstanceStatus:
    instance_id: str
    state: str                  # provisioning | starting | running | stopped | terminated | unknown
    ssh_host: str = ""
    ssh_port: int = 0
    direct_url: str = ""        # if the provider gives an HTTPS endpoint instead of SSH
    dph: Optional[float] = None
    message: str = ""
    label: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class BillingReport:
    month: str
    charges: list = field(default_factory=list)     # [{"ref":..., "amount":..., "ts":..., "note":...}] provider-reported
    balance_usd: Optional[float] = None
    covered_until: Optional[float] = None           # charges are complete up to this timestamp
    note: str = ""


class GPUProvider:
    name = "base"
    display = "Base"
    automated = True       # False: the owner starts/stops compute in the provider console (assisted mode)
    paid = True

    def configured(self) -> tuple[bool, str]:
        raise NotImplementedError

    async def find_offer(self, profile: dict, settings: dict) -> Offer:
        raise NotImplementedError

    async def create(self, offer: Offer, profile: dict, settings: dict, label: str,
                     ssh_public_key: str, vllm_api_key: str) -> str:
        raise NotImplementedError

    async def status(self, instance_id: str) -> InstanceStatus:
        raise NotImplementedError

    async def stop(self, instance_id: str, settings: dict) -> None:
        raise NotImplementedError

    async def list_managed(self, label_prefix: str) -> list[InstanceStatus]:
        return []

    async def billing(self, month: str, month_start: float) -> Optional[BillingReport]:
        return None

    def check_summary(self) -> dict:
        ok, why = self.configured()
        return {"provider": self.name, "configured": ok, "detail": why, "automated": self.automated}
