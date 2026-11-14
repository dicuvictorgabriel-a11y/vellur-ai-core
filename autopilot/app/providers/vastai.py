"""Vast.ai adapter. Endpoints and payloads follow the official `vastai` SDK/CLI 1.11.0 (vastai/api/*.py):

  POST   /api/v0/bundles/                 search offers (filters in JSON body)
  PUT    /api/v0/asks/{offer_id}/         create instance  -> {"success": true, "new_contract": <id>}
  POST   /api/v0/instances/{id}/ssh/      attach an SSH public key to the instance
  GET    /api/v0/instances/{id}/?owner=me instance details  -> {"instances": {...}}
  PUT    /api/v0/instances/{id}/          {"state": "stopped"}  (stop; disk stays and is billed)
  DELETE /api/v0/instances/{id}/          destroy (no further charges for it)
  GET    /api/v1/instances/               list own instances (select_filters, order_by, limit, after_token)
  GET    /api/v0/users/current            account data (credit)
  GET    /api/v0/users/me/invoices        invoices + current charges (sdate/edate, inc_charges)
Auth: "Authorization: Bearer <VAST_API_KEY>". Base URL: https://console.vast.ai (SDK default).
Not verified against a live account in this project: no key was available.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional
from urllib.parse import quote_plus

import httpx

from ..config import VLLM_IMAGE
from .base import BillingReport, GPUProvider, InstanceStatus, Offer, ProviderError

# Runs inside the rented container (runtype ssh_proxy => the image entrypoint is replaced; onstart runs).
# vLLM listens on 127.0.0.1 only; the VPS reaches it through its own SSH tunnel.
ONSTART = " ".join([
    "bash -c 'CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d \" .\");",
    "KV=auto; [ \"${CC:-0}\" -ge 89 ] && KV=fp8;",
    "nohup vllm serve \"$MODEL_REPO\" --host 127.0.0.1 --port 8000 --api-key \"$VLLM_API_KEY\"",
    "--served-model-name vellur-coder --max-model-len \"$MAX_MODEL_LEN\" --gpu-memory-utilization 0.92",
    "--kv-cache-dtype $KV --enable-prefix-caching --enable-auto-tool-choice --tool-call-parser qwen3_coder",
    "--reasoning-parser qwen3 > /root/vllm.log 2>&1 &'",
])

_STATE = {"running": "running", "loading": "starting", "created": "provisioning",
          "exited": "stopped", "stopped": "stopped", "offline": "unknown"}


class VastProvider(GPUProvider):
    name = "vastai"
    display = "Vast.ai"
    automated = True
    paid = True

    def __init__(self, api_key: str, base_url: str = "https://console.vast.ai", live: bool = False,
                 transport: Optional[httpx.AsyncBaseTransport] = None, timeout: float = 30.0):
        self.api_key, self.base, self.live = api_key, base_url.rstrip("/"), live
        self._mocked = transport is not None
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout)

    def configured(self):
        return (True, "ok") if self.api_key else (False, "VAST_API_KEY not set on the server (provider disabled)")

    def _url(self, path: str, query: Optional[dict] = None) -> str:
        if not path.startswith("/api/v"):
            path = "/api/v0" + path
        if query:
            qs = "&".join(f"{k}={quote_plus(v if isinstance(v, str) else json.dumps(v))}" for k, v in query.items())
            return f"{self.base}{path}?{qs}"
        return self.base + path

    async def _req(self, method: str, path: str, query: Optional[dict] = None, body=None, mutate: bool = False):
        if not self.api_key:
            raise ProviderError("config", "VAST_API_KEY not set")
        if mutate and not self.live:
            raise ProviderError("dry_run", f"dry-run: refusing {method} {path}")
        if mutate and os.environ.get("PYTEST_CURRENT_TEST") and not self._mocked:
            raise ProviderError("dry_run", "automated tests may never call a real provider")
        try:
            r = await self._client.request(method, self._url(path, query), json=body,
                                           headers={"Authorization": f"Bearer {self.api_key}",
                                                    "User-Agent": "vellur-autopilot/1"})
        except httpx.TimeoutException:
            raise ProviderError("timeout", f"Vast.ai timeout on {method} {path}")
        except httpx.HTTPError as e:
            raise ProviderError("provider", f"Vast.ai unreachable: {e.__class__.__name__}")
        if r.status_code in (401, 403):
            raise ProviderError("auth", "Vast.ai rejected the API key")
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            raise ProviderError("provider", "Vast.ai rate limit; retry later")
        if r.status_code >= 400:
            raise ProviderError("provider", f"Vast.ai HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            raise ProviderError("provider", "Vast.ai returned non-JSON")

    async def find_offer(self, profile: dict, settings: dict) -> Offer:
        q = {
            "verified": {"eq": True}, "external": {"eq": False}, "rentable": {"eq": True}, "rented": {"eq": False},
            "num_gpus": {"eq": 1},
            "gpu_ram": {"gte": int(profile["min_vram_gb"]) * 1000},            # SDK multiplies GB by 1000
            "dph_total": {"lte": float(settings["max_hourly_usd"])},
            "reliability": {"gte": float(settings["vast_min_reliability"])},
            "cuda_max_good": {"gte": 12.9},                                    # vllm-openai v0.27.1 is cu129
            "order": [["dph_total", "asc"]], "type": "on-demand", "limit": 40,
            "allocated_storage": float(settings["vast_disk_gb"]),
        }
        data = await self._req("POST", "/bundles/", body=q) or {}
        offers = data.get("offers") or []
        if not offers:
            raise ProviderError("capacity", "Vast.ai: no offer matches VRAM, price and reliability limits")
        prefs = [c.strip().upper() for c in str(settings["vast_geo_preference"]).split(",") if c.strip()]

        def geo_rank(o):
            g = str(o.get("geolocation") or "")
            code = g.rsplit(",", 1)[-1].strip().upper() if g else ""
            return prefs.index(code) if code in prefs else len(prefs)

        offers.sort(key=lambda o: (geo_rank(o), float(o.get("dph_total") or 99)))
        o = offers[0]
        dl = float(profile.get("download_gb", 20)) * float(o.get("inet_down_cost") or 0)
        return Offer(str(o["id"]), str(o.get("gpu_name", "")), float(o.get("gpu_ram") or 0) / 1000.0,
                     float(o.get("dph_total") or 0), extra_cost=round(dl, 4),
                     location=str(o.get("geolocation") or ""), reliability=o.get("reliability"), raw=o)

    async def create(self, offer, profile, settings, label, ssh_public_key, vllm_api_key) -> str:
        body = {
            "client_id": "me", "image": VLLM_IMAGE, "disk": float(settings["vast_disk_gb"]), "label": label,
            "env": {"VLLM_API_KEY": vllm_api_key, "MODEL_REPO": profile["model_repo"],
                    "MAX_MODEL_LEN": str(profile["max_model_len"])},
            "onstart": ONSTART, "runtype": "ssh_proxy", "price": None,
            "cancel_unavail": True,
        }
        data = await self._req("PUT", f"/asks/{offer.offer_id}/", body=body, mutate=True) or {}
        iid = data.get("new_contract")
        if not data.get("success") or not iid:
            raise ProviderError("capacity", f"Vast.ai did not create the instance: {str(data)[:200]}")
        iid = str(iid)
        await self._req("POST", f"/instances/{iid}/ssh/", body={"ssh_key": ssh_public_key}, mutate=True)
        return iid

    async def status(self, instance_id: str) -> InstanceStatus:
        data = await self._req("GET", f"/instances/{instance_id}/", query={"owner": "me"})
        row = (data or {}).get("instances")
        if not row:
            return InstanceStatus(instance_id, "terminated")
        return self._row(row)

    def _row(self, row: dict) -> InstanceStatus:
        actual = row.get("actual_status")
        state = _STATE.get(actual or "created", "unknown")
        if row.get("intended_status") in ("stopped", "exited") and state == "running":
            state = "starting"
        return InstanceStatus(str(row.get("id")), state, ssh_host=str(row.get("ssh_host") or ""),
                              ssh_port=int(row.get("ssh_port") or 0), dph=row.get("dph_total"),
                              message=str(row.get("status_msg") or ""), label=str(row.get("label") or ""), raw=row)

    async def stop(self, instance_id: str, settings: dict) -> None:
        if settings.get("vast_idle_action") == "stop":
            await self._req("PUT", f"/instances/{instance_id}/", body={"state": "stopped"}, mutate=True)
        else:
            await self._req("DELETE", f"/instances/{instance_id}/", body={}, mutate=True)

    async def list_managed(self, label_prefix: str) -> list[InstanceStatus]:
        out, params = [], {"select_filters": {}, "order_by": [{"col": "id", "dir": "asc"}], "limit": 25}
        for _ in range(20):
            data = await self._req("GET", "/api/v1/instances/", query=params) or {}
            for row in data.get("instances") or []:
                st = self._row(row)
                if st.label.startswith(label_prefix):
                    out.append(st)
            nxt = data.get("next_token")
            if not nxt:
                break
            params["after_token"] = nxt
        return out

    async def billing(self, month: str, month_start: float) -> Optional[BillingReport]:
        now = time.time()
        user = await self._req("GET", "/users/current") or {}
        inv = await self._req("GET", "/users/me/invoices",
                              query={"owner": "me", "sdate": month_start, "edate": now, "inc_charges": True}) or {}
        charges = []
        for row in inv.get("invoices") or []:
            if row.get("type") != "charge":
                continue
            try:
                amount = abs(float(row.get("amount")))
            except (TypeError, ValueError):
                continue
            ts = float(row.get("timestamp") or row.get("start") or now)
            ref = str(row.get("id") or f"{ts}:{amount}")
            charges.append({"ref": ref, "amount": amount, "ts": ts, "note": str(row.get("description") or "")[:120]})
        bal = user.get("credit")
        return BillingReport(month, charges=charges, balance_usd=float(bal) if bal is not None else None,
                             covered_until=now - 3600,
                             note=f"current (not yet invoiced): {str(inv.get('current'))[:120]}")
