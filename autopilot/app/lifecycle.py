"""GPU lifecycle controller: one persistent session at a time, idempotent start/stop, conservative idle shutdown.

States (persisted in sessions.state):
  searching → provisioning → starting → loading_model → ready   (automated providers)
  attaching → loading_model → ready                              (assisted: HyperAI)
  ready → stopping → stopped                                     (stopped = provider-confirmed for automated ones)
  any → failed                                                   (reason recorded; instance cleanup attempted)
Displayed phase adds processing / idle (derived from live activity) and offline (no session).
"Stopping vLLM" or "closing the tunnel" is never reported as "compute terminated": only a provider status of
stopped/terminated after a stop request sets termination_verified=1.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Callable, Optional

import httpx

from .budget import Budget, month_key, month_start
from .config import MANAGED_LABEL, PROFILES, Env
from .db import DB
from .notify import Notifier
from .providers.base import GPUProvider, ProviderError
from .settings import Settings

log = logging.getLogger("autopilot.lifecycle")

STARTING_STATES = ("searching", "attaching", "provisioning", "starting", "loading_model")
LIVE_STATES = STARTING_STATES + ("ready",)
HEALTH_FAILS_MANUAL_END = 4      # assisted provider: backend unreachable this many ticks → workspace gone
HEALTH_FAILS_WARN = 8            # automated: warn only; monitoring failures never stop a session
STOP_GRACE_SECONDS = 600         # session limit / budget: let active requests finish this long
STOP_VERIFY_ATTEMPTS = 18        # x poll_seconds


class ControlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Controller:
    def __init__(self, db: DB, env: Env, settings: Settings, providers: dict[str, GPUProvider],
                 tunnel, notifier: Notifier, http: Optional[httpx.AsyncClient] = None,
                 clock: Callable[[], float] = time.time):
        self.db, self.env, self.settings, self.providers = db, env, settings, providers
        self.tunnel, self.notifier, self.clock = tunnel, notifier, clock
        self.budget = Budget(db)
        self.http = http or httpx.AsyncClient(timeout=10)
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition()
        self.version = 0
        self.inflight = 0
        self.queued = 0
        self.last_activity = self.clock()
        self.protected_until = 0.0
        self.health_failures = 0
        self.limit_hit_at: Optional[float] = None
        self.backend_url: Optional[str] = None
        self._start_task: Optional[asyncio.Task] = None
        self._stop_task: Optional[asyncio.Task] = None
        self._watchdog: Optional[asyncio.Task] = None
        self._last_billing_sync = 0.0
        self._manual_idle_alerted: Optional[int] = None

    # ── persistence helpers ─────────────────────────────────────────────────────────────────────────
    def current(self) -> Optional[dict]:
        sid = self.db.kv_get("current_session")
        return self.db.one("SELECT * FROM sessions WHERE id=?", (sid,)) if sid else None

    def _save(self, sid: int, **fields) -> dict:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.x(f"UPDATE sessions SET {cols} WHERE id=?", tuple(fields.values()) + (sid,))
        return self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))

    async def _bump(self) -> None:
        async with self._changed:
            self.version += 1
            self._changed.notify_all()

    async def wait_change(self, version: int, timeout: float) -> int:
        async with self._changed:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._changed.wait_for(lambda: self.version != version), timeout)
            return self.version

    async def _set_state(self, sid: int, state: str, reason: str = "", **fields) -> dict:
        s = self._save(sid, state=state, reason=reason, **fields)
        self.db.event("error" if state == "failed" else "info", "state", f"{state}{': ' + reason if reason else ''}",
                      session=sid, provider=s["provider"])
        await self._bump()
        return s

    # ── public API ─────────────────────────────────────────────────────────────────────────────────
    def provider(self, name: str) -> GPUProvider:
        p = self.providers.get(name)
        if not p:
            raise ControlError("config", f"unknown provider {name}")
        return p

    async def start(self, trigger: str = "manual", provider_name: Optional[str] = None) -> dict:
        """Idempotent: returns the live session if one exists; never creates a second one."""
        async with self._lock:
            s = self.current()
            if s and s["state"] in LIVE_STATES:
                return s
            if s and s["state"] == "stopping":
                raise ControlError("busy", "The GPU is stopping; try again in a minute.")
            if s and s["state"] == "failed" and s["instance_id"] and not s["termination_verified"] and not s["dry_run"]:
                raise ControlError("termination_unverified",
                                   "A previous GPU was not confirmed terminated; resolve that first (see alerts).")
            st = self.settings.get()
            if trigger == "request" and not st["enabled"]:
                raise ControlError("disabled", "GPU Autopilot is disabled.")
            name = provider_name or st["preferred_provider"]
            p = self.provider(name)
            ok, why = p.configured()
            if not ok:
                raise ControlError("config", f"{p.display}: {why}")
            if trigger == "request" and p.automated and p.paid and not st["auto_start_paid"]:
                raise ControlError("auto_start_disabled",
                                   f"Automatic start of a paid GPU ({p.display}) is off. Start it from the "
                                   "Autopilot page or enable automatic start.")
            bs = self.budget.status(st, None, self.clock())
            if bs["exhausted"]:
                raise ControlError("budget", "Monthly GPU budget is used up; new GPU sessions are blocked.")
            if st["profile"] not in PROFILES:
                raise ControlError("config", "unknown model profile")
            now = self.clock()
            first = "attaching" if not p.automated else "searching"
            cur = self.db.x("INSERT INTO sessions(provider,profile,trigger,state,created_at,dry_run) VALUES(?,?,?,?,?,?)",
                            (name, st["profile"], trigger, first, now, 0 if (self.env.live or not p.automated) else 1))
            sid = cur.lastrowid
            self.db.kv_set("current_session", sid)
            self.db.event("info", "start", f"start requested ({trigger}) on {p.display}", session=sid)
            self.inflight_reset_for_new_session()
            self._start_task = asyncio.create_task(self._run_start(sid))
            await self._bump()
            return self.current()

    def inflight_reset_for_new_session(self) -> None:
        self.health_failures = 0
        self.limit_hit_at = None
        self.last_activity = self.clock()
        self._manual_idle_alerted = None

    async def stop(self, reason: str = "manual") -> dict:
        """Idempotent. For automated providers waits for provider confirmation in the background."""
        async with self._lock:
            s = self.current()
            if not s:
                return {}
            if s["state"] == "stopping":
                return s
            if s["state"] in ("stopped",) or (s["state"] == "failed" and (s["termination_verified"] or not s["instance_id"] or s["dry_run"])):
                return s
            if self._start_task and not self._start_task.done():
                self._start_task.cancel()
                with contextlib.suppress(BaseException):
                    await self._start_task
            s = await self._set_state(s["id"], "stopping", reason)
            self._stop_task = asyncio.create_task(self._run_stop(s["id"], reason))
            return s

    @contextlib.asynccontextmanager
    async def activity(self):
        """Wraps every proxied inference request (including the whole streaming response)."""
        self.inflight += 1
        self.last_activity = self.clock()
        await self._bump()
        try:
            yield
        finally:
            self.inflight -= 1
            self.last_activity = self.clock()
            await self._bump()

    def protect(self, minutes: float) -> float:
        self.protected_until = max(self.protected_until, self.clock() + 60 * float(minutes))
        self.db.event("info", "protect", f"GPU kept alive for {minutes:g} min by an authorized task")
        return self.protected_until

    async def ensure_ready(self, hold_seconds: Optional[float] = None) -> str:
        """Called by the inference proxy. Returns the backend URL or raises ControlError with a precise code."""
        hold = self.env.request_hold_seconds if hold_seconds is None else hold_seconds
        st = self.settings.get()
        if self.budget.status(st, self.current(), self.clock())["exhausted"]:
            raise ControlError("budget", "Monthly GPU budget is used up; GPU requests are blocked.")
        s = self.current()
        if s and s["state"] == "ready" and self.backend_url:
            if self.limit_hit_at:
                raise ControlError("session_limit", "Session time limit reached; the GPU is being released.")
            return self.backend_url
        if not s or s["state"] not in LIVE_STATES:
            await self.start("request")
        self.queued += 1
        await self._bump()
        deadline = time.monotonic() + hold
        try:
            v = self.version
            while True:
                s = self.current()
                if s and s["state"] == "ready" and self.backend_url:
                    return self.backend_url
                if not s or s["state"] in ("failed", "stopped", "stopping"):
                    code = (s or {}).get("reason", "") or "failed"
                    raise ControlError(code.split(":")[0] or "failed", self.explain(s))
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ControlError("starting", f"Starting your AI environment ({s['state']}). Retry in a minute.")
                v = await self.wait_change(v, min(left, 5.0))
        finally:
            self.queued -= 1
            await self._bump()

    def explain(self, s: Optional[dict]) -> str:
        if not s:
            return "No GPU session."
        r = s.get("reason") or ""
        code, _, msg = r.partition(":")
        return msg.strip() or code or s["state"]

    # ── start / stop workers ──────────────────────────────────────────────────────────────────────
    async def _run_start(self, sid: int) -> None:
        s = self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        p = self.provider(s["provider"])
        st = self.settings.get()
        profile = PROFILES[s["profile"]]
        try:
            if not p.automated:
                await self._attach_assisted(sid, p)
            else:
                offer = await asyncio.wait_for(p.find_offer(profile, st), 90)
                ok, why = self.budget.check_start(st, p.name, offer.dph, offer.extra_cost, self.clock())
                if not ok:
                    raise ProviderError("budget" if "budget" in why else "price",
                                        f"{why}: offer {offer.gpu_name} at ${offer.dph:.3f}/h does not fit the limits")
                self._save(sid, offer=json.dumps(offer.raw)[:4000], gpu_name=offer.gpu_name, vram_gb=offer.vram_gb,
                           dph=offer.dph, extra_cost=offer.extra_cost)
                if not self.env.live:
                    await self._set_state(sid, "stopped", f"dry_run: would rent {offer.gpu_name} "
                                          f"({offer.vram_gb:g} GB) at ${offer.dph:.3f}/h in {offer.location or '?'}; "
                                          "live mode is off", ended_at=self.clock(), termination_verified=1)
                    return
                await self._set_state(sid, "provisioning")
                pub = open(self.env.ssh_key_path + ".pub").read().strip()
                iid = await asyncio.wait_for(p.create(offer, profile, st, f"{MANAGED_LABEL}-{sid}", pub,
                                                      self.env.vllm_api_key), 180)
                # Billing is assumed to start at creation (conservative).
                await self._set_state(sid, "starting", instance_id=iid, billing_started_at=self.clock())
                inst = await self._wait_running(p, iid)
                if inst.direct_url:
                    self.backend_url = inst.direct_url
                else:
                    await self.tunnel.open(inst.ssh_host, inst.ssh_port)
                    self.backend_url = self.tunnel.url
                await self._set_state(sid, "loading_model")
                await self._wait_model(self.env.model_load_timeout_seconds)
            self.last_activity = self.clock()
            await self._set_state(sid, "ready", ready_at=self.clock())
        except asyncio.CancelledError:
            raise
        except ProviderError as e:
            await self._fail(sid, e.code, e.message)
        except asyncio.TimeoutError:
            await self._fail(sid, "timeout", "the provider or the model did not become ready in time")
        except Exception as e:  # never leave a half-started GPU unattended
            log.exception("start failed")
            await self._fail(sid, "provider", f"{e.__class__.__name__}: {e}")

    async def _attach_assisted(self, sid: int, p: GPUProvider) -> None:
        from .providers.hyperai import START_HELP
        host, port = p.endpoint()  # type: ignore[attr-defined]
        trigger = self._row(sid)["trigger"]
        try:
            await self.tunnel.open(host, port)
        except ProviderError as e:
            if e.code == "auth":
                raise
            raise ProviderError("manual", f"HyperAI workspace not reachable at {host}:{port} ({e.message}). {START_HELP}")
        self.backend_url = self.tunnel.url
        await self._set_state(sid, "loading_model", billing_started_at=self.clock())
        # A message must not wait long for a workspace that is not serving; a manual connect may wait for loading.
        wait = 60.0 if trigger == "request" else min(self.env.model_load_timeout_seconds, 900)
        try:
            await self._wait_model(min(wait, self.env.model_load_timeout_seconds))
        except ProviderError as e:
            raise ProviderError("manual", "The HyperAI workspace is reachable but the model is not answering. "
                                "Run the hyperai-serve.sh script in the workspace terminal and wait for READY.") from e

    async def _wait_running(self, p: GPUProvider, iid: str):
        deadline = time.monotonic() + self.env.provision_timeout_seconds
        errors = 0
        while True:
            try:
                inst = await asyncio.wait_for(p.status(iid), 30)
                errors = 0
                if inst.state == "running" and (inst.direct_url or (inst.ssh_host and inst.ssh_port)):
                    return inst
                if inst.state in ("terminated", "stopped"):
                    raise ProviderError("capacity", f"instance {iid} ended during start-up: {inst.message}")
            except (asyncio.TimeoutError, ProviderError) as e:
                if isinstance(e, ProviderError) and e.code in ("capacity", "auth", "config"):
                    raise
                errors += 1
                if errors >= 6:
                    raise ProviderError("provider", f"status of {iid} unavailable: {getattr(e, 'message', e)}")
            if time.monotonic() > deadline:
                raise ProviderError("timeout", f"instance {iid} not running after {self.env.provision_timeout_seconds:.0f}s")
            await asyncio.sleep(self.env.poll_seconds)

    async def health(self) -> bool:
        if not self.backend_url:
            return False
        try:
            r = await self.http.get(f"{self.backend_url}/v1/models",
                                    headers={"Authorization": f"Bearer {self.env.vllm_api_key}"}, timeout=8)
            return r.status_code == 200
        except Exception:
            return False

    async def _wait_model(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while not await self.health():
            if time.monotonic() > deadline:
                raise ProviderError("timeout", "the model server did not answer in time "
                                    "(is hyperai-serve.sh running / did vLLM start?)")
            await asyncio.sleep(self.env.poll_seconds)

    async def _fail(self, sid: int, code: str, message: str) -> None:
        s = await self._set_state(sid, "failed", f"{code}: {message}", ended_at=self.clock())
        await self.tunnel.close()
        self.backend_url = None
        await self.notifier.alert("gpu_failed", f"GPU start failed ({code}): {message}", session=sid)
        if s["instance_id"] and not s["dry_run"]:
            # Something billable may exist: make sure it is terminated.
            self._stop_task = asyncio.create_task(self._run_stop(sid, "cleanup_after_failure", keep_state_failed=True))

    async def _run_stop(self, sid: int, reason: str, keep_state_failed: bool = False) -> None:
        s = self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        p = self.provider(s["provider"])
        st = self.settings.get()
        await self.tunnel.close()
        self.backend_url = None
        if not p.automated:
            await self._set_state(sid, "stopped", f"{reason}: disconnected. Compute termination unverified — "
                                  "stop the workspace in the HyperAI console", ended_at=self.clock())
            await self.notifier.alert("manual_stop", "Disconnected from HyperAI. Stop the workspace in the HyperAI "
                                      "console, otherwise it keeps billing.", session=sid)
            return
        if not s["instance_id"] or s["dry_run"]:
            if not keep_state_failed:
                await self._set_state(sid, "stopped", reason, ended_at=self.clock(), termination_verified=1)
            return
        verified = False
        for attempt in range(STOP_VERIFY_ATTEMPTS):
            try:
                if attempt % 6 == 0:
                    await asyncio.wait_for(p.stop(s["instance_id"], st), 60)
                inst = await asyncio.wait_for(p.status(s["instance_id"]), 30)
                if inst.state in ("terminated", "stopped"):
                    verified = True
                    break
            except (ProviderError, asyncio.TimeoutError) as e:
                self.db.event("warn", "stop", f"stop attempt {attempt + 1} failed: {getattr(e, 'message', e)}", session=sid)
            await asyncio.sleep(self.env.poll_seconds)
        if verified:
            fields = dict(ended_at=self.clock(), termination_verified=1)
            if keep_state_failed:
                self._save(sid, **fields)
                self.db.event("info", "stop", "compute termination confirmed by provider after failure", session=sid)
                await self._bump()
            else:
                await self._set_state(sid, "stopped", f"{reason}: provider confirmed", **fields)
        else:
            await self._set_state(sid, "failed", "termination_unverified: the provider did not confirm the GPU stopped")
            await self.notifier.alert("termination_unverified",
                                      f"Could not confirm that GPU instance {s['instance_id']} on {p.display} stopped. "
                                      "Check the provider console now; Autopilot keeps retrying.", session=sid)

    # ── watchdog ──────────────────────────────────────────────────────────────────────────────────
    def phase(self) -> str:
        s = self.current()
        if not s:
            return "offline"
        if s["state"] == "failed" and (s.get("reason") or "").startswith(("manual", "dry_run")):
            return "offline"   # waiting for the owner (HyperAI) or dry run: not a real failure
        if s["state"] == "ready":
            if self.inflight > 0:
                return "processing"
            return "idle" if self.clock() - self.last_activity > 60 else "ready"
        return s["state"]

    async def tick(self) -> None:
        st = self.settings.get()
        now = self.clock()
        for t in self.budget.crossed_thresholds(st, now):   # first: alerts must not be skipped by an early return
            await self.notifier.alert("budget_threshold", f"GPU budget {t}% used this month "
                                      f"(budget ${st['monthly_budget_usd']:.2f}).", threshold=t)
        s = self.current()
        if s and s["state"] == "ready":
            p = self.provider(s["provider"])
            if await self.health():
                self.health_failures = 0
            else:
                self.health_failures += 1
                if not p.automated and self.health_failures >= HEALTH_FAILS_MANUAL_END:
                    # The workspace went away (stopped by the owner or by HyperAI's idle shutdown).
                    await self.tunnel.close()
                    self.backend_url = None
                    await self._set_state(s["id"], "stopped", "backend_gone: the HyperAI workspace is no longer "
                                          "reachable (stopped?)", ended_at=now - self.health_failures * self.env.tick_seconds)
                    return
                if p.automated and self.health_failures == HEALTH_FAILS_WARN:
                    self.db.event("warn", "health", "model server not answering; GPU kept (monitoring failures never "
                                  "stop a session)", session=s["id"])
                    with contextlib.suppress(Exception):
                        inst = await p.status(s["instance_id"])
                        if inst.ssh_host:
                            await self.tunnel.open(inst.ssh_host, inst.ssh_port)
            idle_for = now - self.last_activity
            busy = self.inflight > 0 or self.queued > 0 or now < self.protected_until
            if not busy and idle_for >= 60 * float(st["idle_timeout_min"]):
                if p.automated:
                    await self.stop("idle")
                    return
                if self._manual_idle_alerted != s["id"]:
                    self._manual_idle_alerted = s["id"]
                    await self.notifier.alert("manual_idle", f"HyperAI GPU idle for {st['idle_timeout_min']} min. "
                                              "Stop the workspace in the HyperAI console to stop billing.", session=s["id"])
            started = s["billing_started_at"] or s["ready_at"] or now
            over_time = (now - started >= 3600 * float(st["max_session_hours"])
                         or self.budget.session_estimate(s, st, now) >= float(st["max_session_usd"]))
            over_budget = self.budget.status(st, s, now)["exhausted"]
            if over_time or over_budget:
                if self.limit_hit_at is None:
                    self.limit_hit_at = now
                    await self.notifier.alert("budget_stop" if over_budget else "session_limit",
                                              ("Budget reached" if over_budget else "Session time or cost limit reached") +
                                              ": new GPU requests are blocked; the GPU is released after active work ends.",
                                              session=s["id"])
                if self.inflight == 0 or now - self.limit_hit_at > STOP_GRACE_SECONDS:
                    await self.stop("budget" if over_budget else "session_limit")
                    return
        await self._retry_unverified()
        await self._reconcile_orphans()
        if now - self._last_billing_sync >= self.env.billing_sync_seconds:
            self._last_billing_sync = now
            await self.sync_billing()

    async def _retry_unverified(self) -> None:
        if self._stop_task and not self._stop_task.done():
            return
        rows = self.db.all("SELECT * FROM sessions WHERE instance_id!='' AND termination_verified=0 AND dry_run=0 "
                           "AND state IN ('failed','stopping')")
        for r in rows:
            p = self.providers.get(r["provider"])
            if p and p.automated:
                self._stop_task = asyncio.create_task(self._run_stop(r["id"], "retry_termination",
                                                                     keep_state_failed=r["state"] == "failed"))
                return

    async def _reconcile_orphans(self) -> None:
        """Any running instance carrying our label that is not the live session is stopped (live mode only)."""
        cur = self.current()
        live_iid = cur["instance_id"] if cur and cur["state"] in LIVE_STATES + ("stopping",) else None
        for p in self.providers.values():
            if not p.automated or not p.configured()[0]:
                continue
            try:
                found = await asyncio.wait_for(p.list_managed(MANAGED_LABEL), 30)
            except Exception:
                continue  # monitoring failure: never act on it
            for inst in found:
                if inst.instance_id == live_iid or inst.state in ("terminated",):
                    continue
                if inst.state == "stopped" and self.settings.get()["vast_idle_action"] == "stop":
                    continue
                if not self.env.live:
                    if self.db.alert_once(month_key(self.clock()), f"orphan_{p.name}_{inst.instance_id}"):
                        await self.notifier.alert("orphan", f"Untracked {p.display} instance {inst.instance_id} "
                                                  f"({inst.state}) found; dry-run, not touching it.")
                    continue
                await self.notifier.alert("orphan", f"Stopping untracked {p.display} instance {inst.instance_id} ({inst.state}).")
                with contextlib.suppress(Exception):
                    await p.stop(inst.instance_id, self.settings.get())

    async def sync_billing(self) -> dict:
        now = self.clock()
        out = {}
        for p in self.providers.values():
            if not p.configured()[0]:
                continue
            try:
                rep = await asyncio.wait_for(p.billing(month_key(now), month_start(now)), 60)
            except Exception as e:
                self.db.event("warn", "billing", f"{p.display}: billing sync failed: {getattr(e, 'message', e)}")
                continue
            if rep is None:
                continue
            new = sum(1 for c in rep.charges if self.budget.record_provider_charge(p.name, c["ref"], c["amount"], c["ts"], c.get("note", "")))
            if rep.covered_until:
                self.db.x("UPDATE sessions SET reconciled=1 WHERE provider=? AND ended_at IS NOT NULL AND ended_at<?",
                          (p.name, rep.covered_until))
            self.db.kv_set(f"balance_{p.name}", {"usd": rep.balance_usd, "ts": now, "note": rep.note})
            out[p.name] = {"new_charges": new, "balance_usd": rep.balance_usd}
            self.db.event("info", "billing", f"{p.display}: synced {new} new charge(s)", balance=rep.balance_usd)
        await self._bump()
        return out

    async def recover(self) -> None:
        """After a controller/VPS restart: continue where the persisted state says we were."""
        s = self.current()
        if not s:
            return
        self.db.event("info", "recover", f"controller restarted; session {s['id']} was {s['state']}")
        p = self.providers.get(s["provider"])
        if not p:
            return
        if s["state"] == "stopping" or (s["state"] == "failed" and s["instance_id"] and not s["termination_verified"] and not s["dry_run"]):
            self._stop_task = asyncio.create_task(self._run_stop(s["id"], "recovery", keep_state_failed=s["state"] == "failed"))
            return
        if s["state"] not in LIVE_STATES:
            return
        if not p.automated:
            self._start_task = asyncio.create_task(self._resume_assisted(s["id"], p))
            return
        if not s["instance_id"]:
            await self._set_state(s["id"], "failed", "recovery: start was interrupted before an instance existed",
                                  ended_at=self.clock())
            return
        self._start_task = asyncio.create_task(self._resume_automated(s["id"], p, s["instance_id"]))

    async def _resume_automated(self, sid: int, p: GPUProvider, iid: str) -> None:
        try:
            inst = await asyncio.wait_for(p.status(iid), 30)
            if inst.state in ("terminated", "stopped"):
                await self._set_state(sid, "stopped", "recovery: provider reports the instance ended",
                                      ended_at=self.clock(), termination_verified=1)
                return
            inst = await self._wait_running(p, iid)
            if inst.direct_url:
                self.backend_url = inst.direct_url
            else:
                await self.tunnel.open(inst.ssh_host, inst.ssh_port)
                self.backend_url = self.tunnel.url
            await self._set_state(sid, "loading_model")
            await self._wait_model(self.env.model_load_timeout_seconds)
            self.last_activity = self.clock()   # restart the idle timer conservatively
            await self._set_state(sid, "ready", ready_at=self._row(sid)["ready_at"] or self.clock())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._fail(sid, getattr(e, "code", "provider"), getattr(e, "message", str(e)))

    async def _resume_assisted(self, sid: int, p: GPUProvider) -> None:
        try:
            await self._attach_assisted(sid, p)
            self.last_activity = self.clock()
            await self._set_state(sid, "ready", ready_at=self.clock())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._set_state(sid, "stopped", f"recovery: HyperAI workspace not reachable ({getattr(e, 'message', e)})",
                                  ended_at=self.clock())

    def _row(self, sid: int) -> dict:
        return self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))

    async def run_watchdog(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("watchdog tick failed")
            await asyncio.sleep(self.env.tick_seconds)

    def start_background(self) -> None:
        self._watchdog = asyncio.create_task(self.run_watchdog())

    async def close(self) -> None:
        for t in (self._watchdog, self._start_task, self._stop_task):
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(BaseException):
                    await t

    PUBLIC_EXPLAIN = {
        "dry_run": "Dry run: no paid GPU is rented.", "manual": "Waiting for the HyperAI workspace to be started.",
        "backend_gone": "The GPU workspace was stopped.", "idle": "Released after inactivity.",
        "session_limit": "Released at the session time limit.", "budget": "Monthly GPU budget reached.",
        "manual_stop": "Released.", "capacity": "No GPU capacity available.", "timeout": "The GPU did not start in time.",
        "auth": "Provider authentication problem.", "config": "Configuration needed.", "provider": "Provider error.",
        "termination_unverified": "Waiting for the provider to confirm the GPU stopped.",
    }

    def public_status(self) -> dict:
        """What anyone may see: no hosts, ports, instance ids, settings, balances or detailed error text."""
        full = self.status()
        s = full["session"] or {}
        code = (s.get("reason") or "").split(":")[0].strip()
        b = full["budget"]
        keep = ("month", "budget_usd", "total_usd", "remaining_usd", "percent", "confirmed_usd",
                "estimated_unconfirmed_usd", "current_session_estimate_usd", "current_rate_usd_per_hour",
                "hours_left_at_current_rate", "hyperai_hours_left_estimate", "note", "api_reported_usd",
                "api_estimated_usd", "subscriptions_usd", "fixed_outside_budget_usd", "all_in_total_usd")
        return {
            "public": True,
            "phase": full["phase"], "live_mode": full["live_mode"], "enabled": full["enabled"],
            "provider": full["provider"],
            "session": {k: s.get(k) for k in ("state", "gpu_name", "vram_gb", "dph", "provider")} if s else None,
            "explain": self.PUBLIC_EXPLAIN.get(code, "") if s.get("state") in ("failed", "stopped") else "",
            "inflight": full["inflight"], "queued": full["queued"],
            "idle_shutdown_in_s": full["idle_shutdown_in_s"], "termination": full["termination"],
            "budget": {k: b[k] for k in keep},
            "version": full["version"],
        }

    def status(self) -> dict:
        st = self.settings.get()
        s = self.current()
        now = self.clock()
        prov = self.providers.get((s or {}).get("provider") or st["preferred_provider"])
        idle_left = None
        if s and s["state"] == "ready" and self.inflight == 0:
            idle_left = max(0, int(60 * float(st["idle_timeout_min"]) - (now - self.last_activity)))
        return {
            "phase": self.phase(),
            "live_mode": self.env.live,
            "enabled": st["enabled"],
            "session": s,
            "explain": self.explain(s) if s and s["state"] in ("failed", "stopped") else "",
            "provider": {"name": prov.name, "display": prov.display, "automated": prov.automated} if prov else None,
            "inflight": self.inflight, "queued": self.queued,
            "last_activity": self.last_activity, "idle_shutdown_in_s": idle_left,
            "protected_until": self.protected_until if self.protected_until > now else None,
            "termination": (None if not s else "confirmed" if s["termination_verified"] else
                            "unverified (manual, HyperAI)" if prov and not prov.automated and s["state"] == "stopped" else
                            "pending" if s["state"] in LIVE_STATES + ("stopping",) or s["instance_id"] else None),
            "budget": self.budget.status(st, s, now),
            "balances": {k: self.db.kv_get(f"balance_{k}") for k in self.providers},
            "providers": [p.check_summary() for p in self.providers.values()],
            "profiles": {k: {"label": v["label"], "model": v["model_repo"], "min_vram_gb": v["min_vram_gb"],
                             "context": v["max_model_len"], "verified": v["verified"]} for k, v in PROFILES.items()},
            "settings": st,
            "version": self.version,
        }
