"""Model router: public model names (vellur-coder, vellur-chat, vellur-max) → ordered targets.

A target is "provider:model-id" (any OpenAI-compatible API) or "gpu" (the GPU Autopilot backend). The first usable
target answers; a later target is tried only when an earlier one fails before sending any data, and a *paid* later
target only if "paid fallback" is enabled. Every request is recorded (tokens, cost, latency). Costs reported by the
provider are kept apart from our own estimates (tokens × price). Paid targets stop when the monthly budget is used up.

Provider keys are write-only: stored on the server, never returned to the browser.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx

from .budget import month_key
from .config import ROLES
from .lifecycle import ControlError, Controller
from .model_intel import image_prices_from_catalog, infer_capabilities, labels, summary

# Base URLs of OpenAI-compatible APIs. Each can be overridden in the dashboard; "custom" providers can be added.
CATALOG: dict = {
    "openrouter": {"display": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "reports_cost": True,
                   "note": "Many open-weight models from many hosts; models ending in :free cost nothing "
                           "(daily request limits). Prepaid credits; per-key spending limits available."},
    "groq": {"display": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "note": "Very fast open-weight models; free tier with rate limits."},
    "cerebras": {"display": "Cerebras", "base_url": "https://api.cerebras.ai/v1",
                 "note": "Very fast open-weight models. Free-tier terms changed in 2026: check before relying on it."},
    "deepseek": {"display": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "note": "DeepSeek's own API (prepaid balance)."},
    "mistral": {"display": "Mistral", "base_url": "https://api.mistral.ai/v1",
                "note": "Mistral / Devstral / Codestral; free experiment tier with limits."},
    "gemini": {"display": "Google Gemini (OpenAI-compatible)",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "note": "Not open-weight. Free tier with limits; free-tier content may be used by Google."},
    "together": {"display": "Together AI", "base_url": "https://api.together.xyz/v1", "note": "Open-weight model host."},
    "deepinfra": {"display": "DeepInfra", "base_url": "https://api.deepinfra.com/v1/openai", "note": "Open-weight model host."},
    "fireworks": {"display": "Fireworks AI", "base_url": "https://api.fireworks.ai/inference/v1", "note": "Open-weight model host."},
    "zai": {"display": "Z.ai (GLM)", "base_url": "https://api.z.ai/api/paas/v4", "note": "GLM models, pay per token."},
    "zai-coding": {"display": "Z.ai GLM Coding Plan", "base_url": "https://api.z.ai/api/coding/paas/v4",
                   "note": "Flat monthly plan endpoint; verify the URL in your Z.ai plan page."},
}
PLANS = ("free", "per_token", "subscription")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,23}$")
RETRYABLE = {402, 404, 408, 409, 425, 429}
API_PATHS = ("chat/completions", "completions", "embeddings", "responses")
HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "te", "upgrade",
       "proxy-authorization", "proxy-connection", "content-encoding", "authorization"}


class RouterError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class _Skip(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class RouterResponse:
    status: int
    headers: dict = field(default_factory=dict)
    content: Optional[bytes] = None
    stream: Optional[AsyncIterator[bytes]] = None
    media_type: Optional[str] = "application/json"


def error_response(code: str, message: str, status: int = 503) -> RouterResponse:
    headers = {"Retry-After": "60"} if code in ("starting", "busy") else {}
    return RouterResponse(status, headers, json.dumps({"error": {
        "message": f"VELLUR AI ({code}): {message}", "type": "vellur_router", "code": code}}).encode())


class _Sniffer:
    """Finds the OpenAI `usage` object in a response while it streams through, without altering it."""

    GEN_RE = re.compile(rb'"id"\s*:\s*"(gen-[A-Za-z0-9_-]{4,100})"')

    def __init__(self, sse: bool):
        self.sse, self.buf, self.found, self.total, self.gen_id = sse, b"", None, 0, ""

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if not self.gen_id:          # OpenRouter's generation id: lets us ask for the real cost if usage never arrives
            m = self.GEN_RE.search(chunk)
            if m:
                self.gen_id = m.group(1).decode()
        if self.sse:
            self.buf += chunk
            *lines, self.buf = self.buf.split(b"\n")
            for line in lines:
                if line.startswith(b"data:") and b'"usage"' in line:
                    try:
                        u = json.loads(line[5:].strip()).get("usage")
                        if isinstance(u, dict):
                            self.found = u
                    except ValueError:
                        pass
            if len(self.buf) > 1_000_000:
                self.buf = b""
        elif self.total <= 16_000_000:
            self.buf += chunk

    def usage(self) -> Optional[dict]:
        if not self.sse and self.buf:
            try:
                u = json.loads(self.buf).get("usage")
                return u if isinstance(u, dict) else None
            except (ValueError, AttributeError):
                return None
        return self.found


def provider_usd(value) -> Optional[float]:
    """A cost reported by the provider, which may arrive as a number or as a string ("0.042")."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 and v == v and v != float("inf") else None


class Router:
    def __init__(self, ctrl: Controller, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.ctrl, self.db, self.env, self.settings = ctrl, ctrl.db, ctrl.env, ctrl.settings
        self.client = httpx.AsyncClient(transport=transport,
                                        timeout=httpx.Timeout(connect=15, read=900, write=120, pool=30))
        self.tokens: dict[str, str] = {}
        self._reconciling: set = set()
        from .policy import Policy
        self.policy = Policy(self)

    # ── providers ───────────────────────────────────────────────────────────────────────────────
    def providers(self) -> dict:
        stored = self.db.kv_get_shared("llm_providers", {}) or {}
        out = {}
        for name in list(CATALOG) + [n for n in stored if n not in CATALOG]:
            base = CATALOG.get(name, {})
            cfg = stored.get(name, {})
            out[name] = {
                "name": name, "custom": name not in CATALOG,
                "display": cfg.get("display") or base.get("display") or name,
                "base_url": (cfg.get("base_url") or base.get("base_url") or "").rstrip("/"),
                "plan": cfg.get("plan", "per_token"), "monthly_usd": float(cfg.get("monthly_usd") or 0),
                "enabled": bool(cfg.get("enabled", True)), "note": base.get("note", ""),
                "reports_cost": bool(base.get("reports_cost")),
                "image_api": cfg.get("image_api", base.get("image_api", "auto")),
            }
        return out

    def key(self, name: str) -> str:
        return (self.db.kv_get_shared("llm_keys", {}) or {}).get(name, "")

    def providers_public(self) -> list:
        keys = self.db.kv_get("llm_keys", {}) or {}
        out = []
        for p in self.providers().values():
            k = keys.get(p["name"], "")
            cat = self.db.kv_get(f"llm_catalog_{p['name']}") or {}
            meta = self._catalog_meta(p["name"])
            caps = {c for m in meta.values() for c in (m.get("capabilities") or [])}
            out.append({**p, "key_set": bool(k), "key_hint": ("…" + k[-4:]) if len(k) >= 12 else "",
                        "catalog_models": len(cat.get("models") or []), "catalog_ts": cat.get("ts"),
                        "capabilities": sorted(caps), "capability_labels": labels(sorted(caps)),
                        "vision_models": sum(1 for m in meta.values() if "vision" in (m.get("capabilities") or [])),
                        "image_models": sum(1 for m in meta.values() if "image_generation" in (m.get("capabilities") or []))})
        return out

    def update_provider(self, name: str, ch: dict) -> None:
        if not NAME_RE.match(name):
            raise RouterError("invalid", "provider name: lowercase letters, digits and - (2–24 characters)")
        stored = self.db.kv_get("llm_providers", {}) or {}
        cfg = dict(stored.get(name, {}))
        if name not in CATALOG and name not in stored and not ch.get("base_url"):
            raise RouterError("invalid", "a custom provider needs a base URL")
        if "base_url" in ch:
            url = str(ch["base_url"] or "").strip().rstrip("/")
            if url and not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?$", url):
                raise RouterError("invalid", "base URL must be an https:// address")
            cfg["base_url"] = url
        if "plan" in ch:
            if ch["plan"] not in PLANS:
                raise RouterError("invalid", f"plan must be one of {', '.join(PLANS)}")
            cfg["plan"] = ch["plan"]
        if "monthly_usd" in ch:
            try:
                cfg["monthly_usd"] = float(ch["monthly_usd"] or 0)
            except (TypeError, ValueError):
                raise RouterError("invalid", "monthly price must be a number")
            if not 0 <= cfg["monthly_usd"] <= 10000:
                raise RouterError("invalid", "monthly price out of range")
        if "enabled" in ch:
            cfg["enabled"] = bool(ch["enabled"])
        if "display" in ch:
            cfg["display"] = str(ch["display"])[:40]
        if "image_api" in ch:
            if ch["image_api"] not in ("auto", "chat", "images"):
                raise RouterError("invalid", "image API must be auto, chat or images")
            cfg["image_api"] = ch["image_api"]
        stored[name] = cfg
        self.db.kv_set("llm_providers", stored)
        if "api_key" in ch:
            keys = self.db.kv_get("llm_keys", {}) or {}
            k = str(ch["api_key"] or "").strip()
            if k:
                if len(k) > 400 or any(c.isspace() for c in k):
                    raise RouterError("invalid", "that does not look like an API key")
                keys[name] = k
            else:
                keys.pop(name, None)
            self.db.kv_set("llm_keys", keys)
        self.db.event("info", "llm", f"model provider {name} updated",
                      fields=sorted(k for k in ch if k != "api_key") + (["api_key"] if "api_key" in ch else []))

    def delete_provider(self, name: str) -> None:
        stored = self.db.kv_get("llm_providers", {}) or {}
        keys = self.db.kv_get("llm_keys", {}) or {}
        stored.pop(name, None)
        keys.pop(name, None)
        self.db.kv_set("llm_providers", stored)
        self.db.kv_set("llm_keys", keys)
        self.db.event("info", "llm", f"model provider {name} removed/reset")

    def _catalog_meta(self, name: str) -> dict:
        return (self.db.kv_get_shared(f"llm_catalog_meta_{name}") or {}).get("models") or {}

    async def catalog(self, name: str) -> list:
        """Fetch the provider's model list (free call). Prices and capabilities are kept when present."""
        p = self.providers().get(name)
        if not p or not p["base_url"]:
            raise RouterError("config", "unknown provider")
        headers = {"Authorization": f"Bearer {self.key(name)}"} if self.key(name) else {}
        try:
            r = await self.client.get(f"{p['base_url']}/models", headers=headers, timeout=30)
        except httpx.HTTPError as e:
            raise RouterError("provider", f"{p['display']} unreachable ({e.__class__.__name__})", 502)
        if r.status_code in (401, 403):
            raise RouterError("auth", f"{p['display']} rejected the API key", 502)
        if r.status_code != 200:
            raise RouterError("provider", f"{p['display']} answered HTTP {r.status_code}", 502)
        try:
            data = r.json()
            rows = data.get("data") if isinstance(data, dict) else data
        except ValueError:
            raise RouterError("provider", "the model list is not JSON", 502)
        models, meta = [], {}
        for m in (rows or [])[:3000]:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            mid = str(m["id"])[:200]
            pin = pout = None
            pr = m.get("pricing")
            if isinstance(pr, dict):
                try:
                    pin, pout = float(pr.get("prompt")) * 1e6, float(pr.get("completion")) * 1e6
                except (TypeError, ValueError):
                    pin = pout = None
            pimage, pimage_in = image_prices_from_catalog(pr)
            caps = infer_capabilities(mid, m, name)
            arch = m.get("architecture") if isinstance(m.get("architecture"), dict) else {}
            outputs = [str(x).lower() for x in (arch.get("output_modalities") or m.get("output_modalities") or [])
                       if isinstance(x, str)][:8]
            models.append({"id": mid, "in": pin, "out": pout,
                           "ctx": m.get("context_length") or m.get("context_window")})
            meta[mid] = {"capabilities": caps, "labels": labels(caps), "image_price": pimage,
                         "image_input_price": pimage_in, "outputs": outputs,
                         "input": pin, "output": pout,
                         "context": m.get("context_length") or m.get("context_window")}
        self.db.kv_set(f"llm_catalog_{name}", {"ts": time.time(), "models": models})
        self.db.kv_set(f"llm_catalog_meta_{name}", {"ts": time.time(), "models": meta})
        return models

    # ── targets ─────────────────────────────────────────────────────────────────────────────────
    def chain(self, role: str, capability: Optional[str] = None) -> list:
        routes = self.settings.get()["routes"]
        targets = list(routes.get(role) or ([] if role == "vellur-image" else routes.get("vellur-coder")) or ["gpu"])
        if capability:
            targets = [t for t in targets if capability in self.capabilities(t)]
        return targets

    def info(self, target: str) -> Optional[dict]:
        if target == "gpu":
            return {"kind": "gpu", "provider": "gpu", "model": "vellur-coder", "free": False,
                    "capabilities": ["chat", "coding", "reasoning", "text_input", "text_output", "tool_call"]}
        name, _, model = target.partition(":")
        p = self.providers().get(name)
        if not p:
            return None
        free = p["plan"] in ("free", "subscription") or (name == "openrouter" and model.endswith(":free"))
        return {"kind": "api", "provider": name, "model": model, "free": free, "cfg": p}

    def capabilities(self, target: str) -> list[str]:
        info = self.info(target)
        if info is None:
            return []
        if info["kind"] == "gpu":
            return list(info["capabilities"])
        meta = self._catalog_meta(info["provider"]).get(info["model"]) or {}
        caps = meta.get("capabilities")
        if caps:
            return list(caps)
        # No catalog metadata: stay conservative. A known code model still gets "coding", but never vision/image.
        return infer_capabilities(info["model"], {}, info["provider"])

    def supports(self, target: str, capability: str) -> bool:
        return capability in self.capabilities(target)

    def can_vision(self, role: str) -> bool:
        return any(self.supports(t, "vision") for t in self.chain(role))

    def can_generate_images(self, role: str = "vellur-image") -> bool:
        return any(self.supports(t, "image_generation") for t in self.chain(role))

    def models(self, budget_exhausted: Optional[bool] = None) -> list:
        rows = []
        if budget_exhausted is None:      # once for the whole list, not once per model (hundreds of catalog entries)
            budget_exhausted = self.budget_exhausted()
        for name, p in self.providers().items():
            cat = self.db.kv_get_shared(f"llm_catalog_{name}") or {}
            meta = self._catalog_meta(name)
            for m in cat.get("models") or []:
                target = f"{name}:{m['id']}"
                caps = self.capabilities(target)
                image = "image_generation" in caps
                rows.append({"target": target, "provider": name, "provider_display": p["display"],
                             "model": m["id"], "id": m["id"], "in": m.get("in"), "out": m.get("out"),
                             "ctx": m.get("ctx"), "image_price": self.image_price(target),
                             "capabilities": caps, "labels": labels(caps),
                             "vision": "vision" in caps, "image_generation": image,
                             "tool_call": "tool_call" in caps, "coding": "coding" in caps,
                             "free": p["plan"] in ("free", "subscription") or (name == "openrouter" and m["id"].endswith(":free")),
                             "usable": self.usable(target, fallback=False, capability="image_generation" if image else None,
                                                   budget_exhausted=budget_exhausted)[0]})
        gpu = self.info("gpu")
        rows.append({"target": "gpu", "provider": "gpu", "provider_display": "GPU Autopilot", "model": "vellur-coder",
                     "id": "vellur-coder", "in": None, "out": None, "ctx": None, "image_price": None,
                     "capabilities": gpu["capabilities"], "labels": labels(gpu["capabilities"]),
                     "vision": False, "image_generation": False, "tool_call": True, "coding": True,
                     "free": False, "usable": self.usable("gpu", fallback=False, budget_exhausted=budget_exhausted)[0]})
        return rows

    def capability_summary(self, budget_exhausted: Optional[bool] = None) -> dict:
        return summary(self.models(budget_exhausted))

    def price(self, target: str) -> Optional[tuple]:
        manual = self.settings.get()["prices"].get(target)
        if manual:
            return float(manual[0]), float(manual[1])
        name, _, model = target.partition(":")
        for m in (self.db.kv_get_shared(f"llm_catalog_{name}") or {}).get("models") or []:
            if m["id"] == model and m.get("in") is not None:
                return float(m["in"]), float(m["out"])
        return None

    def image_price(self, target: str) -> Optional[float]:
        manual = self.settings.get().get("image_prices", {}).get(target)
        if manual is not None:
            return float(manual)
        name, _, model = target.partition(":")
        meta = self._catalog_meta(name).get(model) or {}
        value = meta.get("image_price")
        return float(value) if isinstance(value, (int, float)) else None

    def image_outputs(self, target: str) -> list[str]:
        """Output modalities the provider's catalog declares for this model ([] when unknown)."""
        name, _, model = target.partition(":")
        return list((self._catalog_meta(name).get(model) or {}).get("outputs") or [])

    def budget_exhausted(self) -> bool:
        return bool(self.ctrl.budget.status(self.settings.get(), None, self.ctrl.clock())["exhausted"])

    def usable(self, target: str, fallback: bool, capability: Optional[str] = None,
               budget_exhausted: Optional[bool] = None) -> tuple:
        """`budget_exhausted`: pass it when checking many targets at once, so the budget is computed only once."""
        st = self.settings.get()
        info = self.info(target)
        if info is None:
            return False, "config", "unknown model provider"
        if capability and not self.supports(target, capability):
            label = capability.replace("_", " ")
            return False, "capability", f"this model does not declare {label} support"
        if info["kind"] == "gpu":
            if fallback and not st["paid_fallback"] and self.ctrl.phase() not in ("ready", "processing", "idle"):
                return False, "policy", "paid fallback is off (no GPU is running)"
            return True, "", ""
        cfg = info["cfg"]
        if not cfg["enabled"]:
            return False, "config", "provider is disabled"
        if not cfg["base_url"]:
            return False, "config", "provider has no base URL"
        if not self.key(info["provider"]):
            return False, "config", f"no API key saved for {cfg['display']}"
        if info["free"]:
            return True, "", ""
        if self.budget_exhausted() if budget_exhausted is None else budget_exhausted:
            return False, "budget", "monthly budget is used up; paid model APIs are blocked"
        if capability == "image_generation":
            if self.image_price(target) is None and not cfg["reports_cost"]:
                return False, "config", "no image price known: set one (Models → Image prices) so costs can be tracked"
        elif self.price(target) is None and not cfg["reports_cost"]:
            return False, "config", "no price known for this model: set one (Models → Prices) so costs can be tracked"
        if fallback and not st["paid_fallback"]:
            return False, "policy", "paid fallback is off"
        return True, "", ""

    # ── access tokens for the internal proxy ────────────────────────────────────────────────────
    def issue_token(self, tag: str) -> str:
        tok = "vtask-" + secrets.token_urlsafe(32)
        self.tokens[tok] = tag
        return tok

    def revoke_tag(self, tag: str) -> None:
        for t in [t for t, g in self.tokens.items() if g == tag]:
            self.tokens.pop(t, None)

    def authorize(self, bearer: str) -> Optional[str]:
        """Returns the usage tag for this caller, or None if not allowed."""
        internal = self.env.vllm_api_key
        if bearer and bearer in self.tokens:
            return self.tokens[bearer]
        if not internal or (bearer and secrets.compare_digest(bearer.encode(), internal.encode())):
            return ""
        return None

    # ── accounting ──────────────────────────────────────────────────────────────────────────────
    def cost(self, target: str, info: dict, usage: Optional[dict]) -> tuple:
        if info["kind"] == "gpu":
            return 0.0, "gpu"
        if info["free"]:
            return 0.0, "plan"
        if usage is None:
            return 0.0, "unknown"
        provider_cost = provider_usd(usage.get("cost"))
        if provider_cost is not None:
            return provider_cost, "provider"
        if int(usage.get("images") or 0):
            # an image is never priced with the token rate: either a known image price or "unpriced"
            if self.image_price(target) is not None:
                return int(usage["images"]) * float(self.image_price(target)), "estimate"
            return 0.0, "unpriced"
        pr = self.price(target)
        if pr:
            return (int(usage.get("prompt_tokens") or 0) * pr[0] + int(usage.get("completion_tokens") or 0) * pr[1]) / 1e6, "estimate"
        return 0.0, "unpriced"

    def record(self, role: str, target: str, info: dict, ok: bool, status: int, usage: Optional[dict],
               t0: float, tag: str, error: str = "", images: int = 0) -> int:
        u = dict(usage or {})
        if images and not u.get("images"):
            u["images"] = images
        # no usage at all (a stream cut before its last chunk) is "unknown", never a $0 estimate
        cost, source = self.cost(target, info, u if (usage is not None or images) else None) if ok else (0.0, "")
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0) if isinstance(u.get("prompt_tokens_details"), dict) else 0
        now = self.ctrl.clock()
        cur = self.db.x("INSERT INTO usage(ts,month,role,target,provider,model,ok,status,in_tokens,out_tokens,cached_tokens,"
                        "cost_usd,cost_source,latency_ms,tag,error,images) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (now, month_key(now), role, target, info["provider"], info["model"], 1 if ok else 0, status,
                         int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), int(cached), cost, source,
                         int((time.monotonic() - t0) * 1000), tag, error[:300], int(u.get("images") or 0)))
        return int(getattr(cur, "lastrowid", 0) or 0)

    # ── reconciliation: a stream stopped early never receives its usage, but the provider still bills it ─────
    RECONCILE_DELAYS = (3, 10, 30, 90)

    def reconcile_later(self, row_id: int, info: dict, gen_id: str) -> None:
        if not (row_id and gen_id and info.get("provider") == "openrouter"):
            return
        task = asyncio.get_running_loop().create_task(self._reconcile(row_id, info, gen_id))
        self._reconciling.add(task)
        task.add_done_callback(self._reconciling.discard)

    async def _reconcile(self, row_id: int, info: dict, gen_id: str) -> None:
        """Ask OpenRouter what generation `gen_id` really cost (its stats appear a few seconds later)."""
        base, key = info["cfg"]["base_url"], self.key(info["provider"])
        for delay in self.RECONCILE_DELAYS:
            await asyncio.sleep(delay)
            try:
                r = await self.client.get(f"{base}/generation", params={"id": gen_id},
                                          headers={"Authorization": f"Bearer {key}"}, timeout=20)
                data = r.json().get("data") if r.status_code == 200 else None
            except (httpx.HTTPError, ValueError, AttributeError):
                data = None
            cost = provider_usd((data or {}).get("total_cost")) if isinstance(data, dict) else None
            if cost is None:
                continue
            tin = int(data.get("tokens_prompt") or data.get("native_tokens_prompt") or 0)
            tout = int(data.get("tokens_completion") or data.get("native_tokens_completion") or 0)
            self.db.x("UPDATE usage SET cost_usd=?, cost_source='provider', in_tokens=MAX(in_tokens,?), "
                      "out_tokens=MAX(out_tokens,?) WHERE id=?", (cost, tin, tout, row_id))
            return

    def tag_cost(self, tag: str) -> float:
        r = self.db.one("SELECT SUM(cost_usd) AS c FROM usage WHERE tag=?", (tag,))
        return float((r or {}).get("c") or 0)

    def tag_costs(self, tag: str) -> dict:
        """The same money as tag_cost, split by how sure we are: provider-reported (confirmed), our own estimate,
        free (plan / own GPU), and requests whose price is unknown (counted, never shown as $0 confirmed)."""
        out = {"confirmed_usd": 0.0, "estimated_usd": 0.0, "free_requests": 0, "unpriced_requests": 0, "requests": 0}
        for r in self.db.all("SELECT cost_source, COUNT(*) AS n, SUM(cost_usd) AS c FROM usage WHERE tag=? GROUP BY cost_source", (tag,)):
            src, n, c = r["cost_source"] or "", int(r["n"] or 0), float(r["c"] or 0)
            out["requests"] += n
            if src == "provider":
                out["confirmed_usd"] += c
            elif src in ("plan", "gpu"):
                out["free_requests"] += n
            elif src == "estimate":
                out["estimated_usd"] += c
            else:
                out["unpriced_requests"] += n
                out["estimated_usd"] += c
        out["confirmed_usd"], out["estimated_usd"] = round(out["confirmed_usd"], 4), round(out["estimated_usd"], 4)
        return out

    # ── dispatch ────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _has_image(payload: dict) -> bool:
        for m in payload.get("messages") or []:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in content):
                return True
        return False

    async def handle(self, method: str, path: str, query: str, headers: dict, body: bytes, tag: str = "",
                     capability: Optional[str] = None) -> RouterResponse:
        path = path.strip("/")
        if method == "GET":
            if path == "models":
                return RouterResponse(200, {}, json.dumps({"object": "list", "data": [
                    {"id": r, "object": "model", "owned_by": "vellur"} for r in ROLES]}).encode())
            return error_response("not_found", "unknown path", 404)
        try:
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            return error_response("invalid_request", "the request body is not JSON", 400)
        role = payload.get("model") or "vellur-coder"
        if role not in ROLES:
            return error_response("model_not_found", f"unknown model {role!r}; available: {', '.join(ROLES)}", 404)
        if capability is None:
            if role == "vellur-image" or (set(payload.get("modalities") or []) & {"image", "image_url"}):
                capability = "image_generation"
            elif self._has_image(payload):
                capability = "vision"
        errors = []
        targets, decision = self.policy.plan(role, capability, payload, tag)
        auto = decision["policy"] != "manual"
        did = self.policy.log(tag, role, decision, targets[0] if targets else "") if auto else 0
        if auto and not targets:
            return error_response("capability", decision["reason"] + " " + "; ".join(
                f"{x['target']}: {x['why']}" for x in decision.get("rejected", [])))
        for i, target in enumerate(targets):
            # automatic modes: the owner authorised choosing among the pool within the allowance, so a later target is
            # not a "paid fallback"; the reservation below is the spending guard instead.
            ok, code, why = self.usable(target, fallback=i > 0 and not auto, capability=capability)
            if not ok:
                errors.append((target, code, why))
                continue
            rid = 0
            if auto:
                rid, why = self.policy.reserve(tag, target, self.policy.expected_usd(target, role, payload) or 0.0)
                if rid is None:
                    errors.append((target, "allowance", why))
                    continue
            try:
                resp = await self._send(target, role, path, query, headers, payload, tag, rid, did)
                if did and i:
                    self.db.x("UPDATE routing_decisions SET chosen=?, reason=reason||? WHERE id=?",
                              (target, f" Used {target} after: " + "; ".join(f"{t}: {w}" for t, _, w in errors), did))
                if did:
                    resp.headers["X-Vellur-Decision"] = str(did)
                return resp
            except _Skip as e:
                self.policy.release(rid)
                errors.append((target, e.code, e.message))
        self.policy.outcome(did, "failed")
        if len(errors) == 1:
            target, code, why = errors[0]
            if target == "gpu" and code == "config" and not any(self.key(n) for n in self.providers()):
                why = ("No inference backend is configured yet. Add a model provider key in the workspace "
                       "(Models), or set up a GPU provider. " + why)
            return error_response(code, why if target == "gpu" else f"{target}: {why}")
        return error_response("no_backend", "no backend could answer — " + "; ".join(f"{t}: {w}" for t, _, w in errors))

    async def _send(self, target: str, role: str, path: str, query: str, headers: dict, payload: dict, tag: str,
                    rid: int = 0, did: int = 0) -> RouterResponse:
        info = self.info(target)
        t0 = time.monotonic()
        up = dict(payload)
        up["model"] = info["model"]
        scope = None
        if info["kind"] == "gpu":
            try:
                base = await self.ctrl.ensure_ready()
            except ControlError as e:
                raise _Skip(e.code, e.message)
            auth = self.env.vllm_api_key
            scope = self.ctrl.activity()
            await scope.__aenter__()
        else:
            if path not in API_PATHS:
                raise _Skip("config", f"path /v1/{path} is not supported for model APIs")
            base, auth = info["cfg"]["base_url"], self.key(info["provider"])
            if up.get("stream") and path in ("chat/completions", "completions"):
                up["stream_options"] = {**(up.get("stream_options") or {}), "include_usage": True}
            if info["provider"] == "openrouter":
                up["usage"] = {"include": True}
        h = {k: v for k, v in headers.items() if k.lower() not in HOP}
        h["Authorization"] = f"Bearer {auth}"
        h["Content-Type"] = "application/json"
        url = f"{base}/v1/{path}" if info["kind"] == "gpu" else f"{base}/{path}"
        if query:
            url += "?" + query
        try:
            r = await self.client.send(self.client.build_request("POST", url, content=json.dumps(up).encode(), headers=h), stream=True)
        except httpx.HTTPError as e:
            if scope:
                await scope.__aexit__(None, None, None)
            self.record(role, target, info, False, 0, None, t0, tag, e.__class__.__name__)
            raise _Skip("provider", f"did not answer ({e.__class__.__name__})")
        if info["kind"] == "api" and (r.status_code in RETRYABLE or r.status_code in (401, 403) or r.status_code >= 500):
            text = (await r.aread()).decode(errors="replace")[:300]
            await r.aclose()
            self.record(role, target, info, False, r.status_code, None, t0, tag, text)
            code = "auth" if r.status_code in (401, 403) else "provider_credit" if r.status_code == 402 else "provider"
            hint = {401: "API key rejected", 403: "API key rejected", 402: "no credit left at the provider",
                    404: "model not found at the provider", 429: "rate limited"}.get(r.status_code, f"HTTP {r.status_code}")
            raise _Skip(code, f"{hint}: {text[:300]}")
        sse = "text/event-stream" in r.headers.get("content-type", "")
        sniff = _Sniffer(sse)
        status = r.status_code

        async def relay():
            try:
                async for chunk in r.aiter_bytes():
                    sniff.feed(chunk)
                    yield chunk
            finally:
                await r.aclose()
                if scope:
                    await scope.__aexit__(None, None, None)
                usage = sniff.usage()
                row = self.record(role, target, info, 200 <= status < 300, status, usage, t0, tag)
                self.policy.release(rid)
                self.policy.outcome(did, "ok" if 200 <= status < 300 else "failed")
                if 200 <= status < 300 and provider_usd((usage or {}).get("cost")) is None:
                    self.reconcile_later(row, info, sniff.gen_id)

        out_headers = {k: v for k, v in r.headers.items() if k.lower() not in HOP}
        out_headers["X-Vellur-Target"] = target
        return RouterResponse(status, out_headers, None, relay(), r.headers.get("content-type"))

    async def probe(self, target: str) -> dict:
        """One tiny real request to a target (used by the "Test" button). Costs a few tokens on paid models."""
        info = self.info(target)
        if info is None or info["kind"] != "api":
            raise RouterError("invalid", "test works for provider:model targets")
        ok, code, why = self.usable(target, fallback=False)
        if not ok:
            return {"ok": False, "code": code, "message": why}
        t0 = time.monotonic()
        try:
            resp = await self._send(target, "vellur-chat", "chat/completions", "", {},
                                    {"messages": [{"role": "user", "content": "Reply with the single word: ready"}],
                                     "max_tokens": 400}, "probe")
        except _Skip as e:
            return {"ok": False, "code": e.code, "message": e.message}
        data = b"".join([c async for c in resp.stream])
        ms = int((time.monotonic() - t0) * 1000)
        try:
            j = json.loads(data)
            msg = j["choices"][0]["message"]
            return {"ok": resp.status == 200, "latency_ms": ms, "usage": j.get("usage"),
                    "answer": (msg.get("content") or "").strip()[:200] or "(empty; reasoning only)"}
        except (ValueError, KeyError, IndexError, TypeError):
            return {"ok": False, "code": "provider", "latency_ms": ms, "message": data.decode(errors="replace")[:300]}
