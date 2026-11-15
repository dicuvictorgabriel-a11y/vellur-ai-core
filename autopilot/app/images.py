"""Image generation through any configured OpenAI-compatible provider.

Two provider shapes are supported:
  * `chat`    — image-output models that answer `/chat/completions` with `modalities: ["image", "text"]`
                (OpenRouter's image models use this shape);
  * `images`  — the classic `/images/generations` endpoint (OpenAI and compatible providers).

Nothing is generated unless an explicit image target is configured and declares image-output support. Paid requests
require confirmation, and a per-request ceiling is enforced before the provider is called. Keys stay on the server.
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .attachments import IMAGE_MIMES, _image_size
from .lifecycle import Controller
from .router import Router, RouterError

ID_RE = re.compile(r"^img_[a-f0-9]{32}$")
SIZE_RE = re.compile(r"^(\d{3,4})x(\d{3,4})$")
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PROMPT = 4000
MAX_IMAGES = 4
IMAGE_RE = re.compile(
    r"(?:\b(?:generate|create|make|draw|design|render|paint|illustrate|imagine|produce|show me|give me)\b"
    r"[^\n]{0,80}\b(?:image|picture|photo|illustration|logo|artwork|poster|icon|avatar|wallpaper|banner|render)\b"
    r"|\b(?:image|picture|photo|illustration|logo|artwork|poster|icon|avatar|wallpaper|banner)\b"
    r"[^\n]{0,50}\b(?:of|with|showing|for)\b"
    r"|^\s*(?:please\s+)?(?:draw|paint|illustrate|sketch)\s+(?:me\s+)?(?:a|an|the)\s+"
    r"(?!(?:conclusion|comparison|distinction|parallel|blank)\b)"
    r"|^\s*/image\b)", re.I)
UNDERSTAND_RE = re.compile(r"\b(?:what(?:'s| is)|describe|analyse|analyze|explain|read|look at|identify|compare|"
                           r"summari[sz]e|ocr|text in)\b[^\n]{0,40}\b(?:image|picture|photo|screenshot|attachment)\b", re.I)


class ImageError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, data: Optional[dict] = None):
        super().__init__(message)
        self.code, self.message, self.status, self.data = code, message, status, data or {}


def wants_image(text: str, has_images: bool = False) -> bool:
    text = str(text or "")
    if not IMAGE_RE.search(text):
        return False
    return not (has_images and UNDERSTAND_RE.search(text))


@dataclass
class ImageResult:
    data: bytes
    mime: str
    width: int = 0
    height: int = 0
    revised_prompt: str = ""
    usage: dict = field(default_factory=dict)


def _sniff_image(data: bytes) -> tuple[str, int, int]:
    mime = ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    if mime not in IMAGE_MIMES:
        raise ImageError("invalid_image", "the provider returned data that is not a supported image", 502)
    size = _image_size(data, mime)
    return mime, int(size.get("width") or 0), int(size.get("height") or 0)


def _data_url(value: str) -> Optional[bytes]:
    if not isinstance(value, str) or not value.startswith("data:"):
        return None
    try:
        head, payload = value.split(",", 1)
        if ";base64" not in head:
            return None
        return base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        return None


def _safe_remote(value: str) -> bool:
    try:
        u = urlparse(value)
    except ValueError:
        return False
    if u.scheme != "https" or not u.hostname:
        return False
    host = u.hostname.lower()
    if host in ("localhost", "localhost.localdomain"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)
    except ValueError:
        return True


class ImageStore:
    def __init__(self, ctrl: Controller):
        self.ctrl, self.db, self.env = ctrl, ctrl.db, ctrl.env
        self.root = os.path.join(ctrl.env.data_dir, "images")
        os.makedirs(self.root, mode=0o700, exist_ok=True)

    def public(self, row: dict) -> dict:
        meta = row.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        return {"id": row["id"], "prompt": row["prompt"], "mime": row["mime"], "size": int(row["size"]),
                "width": int(row.get("width") or 0), "height": int(row.get("height") or 0),
                "created": row["created"], "target": row["target"], "provider": row["provider"],
                "model": row["model"], "cost_usd": float(row.get("cost_usd") or 0),
                "cost_source": row.get("cost_source") or "", "meta": meta,
                "url": f"/autopilot/api/images/{row['id']}/content",
                "preview_url": f"/autopilot/api/images/{row['id']}/content"}

    def get(self, iid: str) -> Optional[dict]:
        if not ID_RE.match(iid or ""):
            return None
        row = self.db.one("SELECT * FROM generated_images WHERE id=?", (iid,))
        if row:
            try:
                row["meta"] = json.loads(row.get("meta") or "{}")
            except (TypeError, ValueError):
                row["meta"] = {}
        return row

    def path(self, row: dict) -> str:
        if not ID_RE.match(row.get("id", "")):
            raise ValueError("invalid image id")
        folder = os.path.realpath(os.path.join(self.root, row["id"]))
        p = os.path.realpath(os.path.join(folder, row["id"] + _ext(row["mime"])))
        if not p.startswith(folder + os.sep):
            raise ValueError("invalid image path")
        return p

    def save(self, result: ImageResult, prompt: str, target: str, provider: str, model: str,
             cost_usd: float = 0.0, cost_source: str = "", conv_id: Optional[int] = None,
             task_id: Optional[int] = None, meta: Optional[dict] = None) -> dict:
        if not result.data:
            raise ImageError("invalid_image", "the provider returned an empty image", 502)
        if len(result.data) > MAX_IMAGE_BYTES:
            raise ImageError("too_large", "the generated image is larger than 25 MB", 502)
        iid = "img_" + secrets.token_hex(16)
        folder = os.path.join(self.root, iid)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        final = os.path.join(folder, iid + _ext(result.mime))
        tmp = final + ".tmp"
        row = {"id": iid, "created": time.time(), "prompt": prompt[:MAX_PROMPT], "provider": provider,
               "model": model, "target": target, "mime": result.mime, "size": len(result.data),
               "width": result.width, "height": result.height,
               "sha256": hashlib.sha256(result.data).hexdigest(), "cost_usd": cost_usd,
               "cost_source": cost_source, "conv_id": conv_id, "task_id": task_id,
               "meta": json.dumps(meta or {})}
        try:
            with open(tmp, "wb") as f:
                f.write(result.data)
            os.replace(tmp, final)
            os.chmod(final, 0o600)
            self.db.x("INSERT INTO generated_images(id,created,prompt,provider,model,target,mime,size,width,height,"
                      "sha256,cost_usd,cost_source,conv_id,task_id,meta) VALUES(:id,:created,:prompt,:provider,:model,"
                      ":target,:mime,:size,:width,:height,:sha256,:cost_usd,:cost_source,:conv_id,:task_id,:meta)", row)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            with contextlib.suppress(OSError):
                shutil.rmtree(folder)
            raise
        return self.get(iid)

    def delete_for_conversation(self, cid: int) -> None:
        for row in self.db.all("SELECT * FROM generated_images WHERE conv_id=?", (cid,)):
            self._delete(row)

    def delete_for_task(self, tid: int) -> None:
        for row in self.db.all("SELECT * FROM generated_images WHERE task_id=?", (tid,)):
            self._delete(row)

    def _delete(self, row: dict) -> None:
        with contextlib.suppress(OSError, ValueError):
            shutil.rmtree(os.path.dirname(self.path(row)))
        self.db.x("DELETE FROM generated_images WHERE id=?", (row["id"],))


def _ext(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(mime, ".png")


class ImageService:
    def __init__(self, ctrl: Controller, router: Router, store: Optional[ImageStore] = None):
        self.ctrl, self.router, self.db, self.settings = ctrl, router, ctrl.db, ctrl.settings
        self.store = store or ImageStore(ctrl)

    def _target(self, role: str) -> Optional[str]:
        for i, target in enumerate(self.router.chain(role)):
            ok, _, _ = self.router.usable(target, fallback=i > 0, capability="image_generation")
            if ok:
                return target
        return None

    def status(self, role: str = "vellur-image") -> dict:
        target = self._target(role)
        if not target:
            return {"available": False, "reason": "no image-generation model is configured",
                    "role": role, "target": None}
        info = self.router.info(target) or {}
        return {"available": True, "role": role, "target": target, "provider": info.get("provider"),
                "model": info.get("model"), "free": bool(info.get("free")),
                "price_per_image": self.router.image_price(target),
                "capabilities": self.router.capabilities(target)}

    def estimate(self, prompt: str = "", role: str = "vellur-image", size: str = "1024x1024",
                 n: int = 1, max_usd: Optional[float] = None) -> dict:
        prompt, size, n = str(prompt or "").strip(), str(size or "1024x1024"), int(n or 1)
        if len(prompt) > MAX_PROMPT:
            raise ImageError("invalid", f"the image prompt is limited to {MAX_PROMPT} characters")
        if not SIZE_RE.match(size):
            raise ImageError("invalid", "image size must look like 1024x1024")
        w, h = (int(x) for x in SIZE_RE.match(size).groups())
        if not (256 <= w <= 2048 and 256 <= h <= 2048):
            raise ImageError("invalid", "image dimensions must be between 256 and 2048 pixels")
        if not 1 <= n <= MAX_IMAGES:
            raise ImageError("invalid", f"ask for 1–{MAX_IMAGES} images at a time")
        target = self._target(role)
        if not target:
            raise ImageError("image_unavailable",
                             "No image generation model is configured. Add an image-output model in Models, then set "
                             "the vellur-image route and save its key.", 400)
        info = self.router.info(target) or {}
        price = self.router.image_price(target)
        free = bool(info.get("free"))
        est = 0.0 if free else (price * n if price is not None else None)
        limit = float(self.settings.get().get("image_max_usd", 1.0) if max_usd is None else max_usd)
        if est is not None and est > limit:
            raise ImageError("image_budget",
                             f"this request would cost about ${est:.4f}, above the ${limit:.2f} image limit", 400)
        return {"available": True, "target": target, "provider": info.get("provider"), "model": info.get("model"),
                "free": free, "price_per_image": price, "n": n, "size": size,
                "estimated_usd": est, "limit_usd": limit,
                "requires_confirmation": bool((not free) and self.settings.get().get("image_confirm_paid", True)),
                "cost_known": free or price is not None}

    async def generate(self, prompt: str, role: str = "vellur-image", size: str = "1024x1024", n: int = 1,
                       confirm: bool = False, tag: str = "image", conv_id: Optional[int] = None,
                       task_id: Optional[int] = None, max_usd: Optional[float] = None) -> dict:
        prompt = str(prompt or "").strip()
        if not prompt:
            raise ImageError("invalid", "write a description of the image you want")
        estimate = self.estimate(prompt, role, size, n, max_usd)
        if estimate["requires_confirmation"] and not confirm:
            raise ImageError("image_confirmation",
                             "This image is generated by a paid model. Confirm the estimated cost to continue.",
                             409, estimate)
        target = estimate["target"]
        info = self.router.info(target) or {}
        t0 = time.monotonic()
        try:
            results, usage = await self._call(target, info, prompt, size, n)
        except ImageError as e:
            self.router.record(role, target, info, False, e.status, None, t0, tag, e.message)
            raise
        except Exception as e:  # noqa: BLE001
            self.router.record(role, target, info, False, 0, None, t0, tag, f"{e.__class__.__name__}: {e}")
            raise ImageError("provider", f"image provider failed ({e.__class__.__name__})", 502)
        if not results:
            self.router.record(role, target, info, False, 502, None, t0, tag, "provider returned no images")
            raise ImageError("invalid_image", "the provider answered but returned no image", 502)
        provider_cost = usage.get("cost") if isinstance(usage, dict) else None
        cost, source = 0.0, "unpriced"
        got_provider_cost = False
        if provider_cost is not None:
            try:
                cost, source = float(provider_cost), "provider"
                got_provider_cost = True
            except (TypeError, ValueError):
                pass
        if not got_provider_cost:
            if estimate["free"]:
                cost, source = 0.0, "plan"
            elif estimate["price_per_image"] is not None:
                cost, source = estimate["price_per_image"] * len(results), "estimate"
        usage = {**(usage or {}), "images": len(results)}
        self.router.record(role, target, info, True, 200, usage, t0, tag, images=len(results))
        saved = []
        per_image = cost / len(results) if results else 0.0
        for result in results:
            row = self.store.save(result, prompt, target, info.get("provider", ""), info.get("model", ""),
                                  per_image, source, conv_id=conv_id, task_id=task_id,
                                  meta={"revised_prompt": result.revised_prompt} if result.revised_prompt else {})
            saved.append(self.store.public(row))
        return {"images": saved, "estimate": estimate, "cost_usd": cost, "cost_source": source,
                "target": target, "provider": info.get("provider"), "model": info.get("model")}

    async def _call(self, target: str, info: dict, prompt: str, size: str, n: int) -> tuple[list[ImageResult], dict]:
        cfg = info.get("cfg") or {}
        base = str(cfg.get("base_url") or "").rstrip("/")
        key = self.router.key(info["provider"])
        if not base or not key:
            raise ImageError("config", "the image provider has no base URL or API key", 400)
        mode = cfg.get("image_api") or "auto"
        if mode == "auto":
            mode = "chat" if (info["provider"] == "openrouter" or
                              ("image_generation" in self.router.capabilities(target) and
                               info["provider"] not in ("openai",))) else "images"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if mode == "chat":
            url = f"{base}/chat/completions"
            body: dict[str, Any] = {"model": info["model"], "messages": [{"role": "user", "content": prompt}],
                                    "modalities": ["image", "text"], "n": n}
        else:
            url = f"{base}/images/generations"
            body: dict[str, Any] = {"model": info["model"], "prompt": prompt, "n": n, "size": size,
                                    "response_format": "b64_json"}
        if info["provider"] == "openrouter":
            body["usage"] = {"include": True}
        try:
            r = await self.router.client.post(url, headers=headers, json=body, timeout=300)
        except httpx.HTTPError as e:
            raise ImageError("provider", f"the image provider did not answer ({e.__class__.__name__})", 502)
        if r.status_code in (401, 403):
            raise ImageError("auth", "the image provider rejected the API key", 502)
        if r.status_code == 402:
            raise ImageError("provider_credit", "the image provider has no credit left", 502)
        if r.status_code == 429:
            raise ImageError("rate_limited", "the image provider is rate limiting requests", 502)
        if r.status_code >= 400:
            try:
                error = r.json().get("error")
                message = error.get("message") if isinstance(error, dict) else (error or r.text)
            except ValueError:
                message = r.text
            raise ImageError("provider", f"image provider answered HTTP {r.status_code}: {str(message)[:300]}", 502)
        try:
            data = r.json()
        except ValueError:
            raise ImageError("invalid_image", "the image provider did not return JSON", 502)
        refs = []
        for choice in data.get("choices") or []:
            msg = choice.get("message") if isinstance(choice, dict) else None
            if not isinstance(msg, dict):
                continue
            for item in msg.get("images") or []:
                refs.append(item)
            content = msg.get("content")
            if isinstance(content, list):
                refs.extend(p for p in content if isinstance(p, dict) and p.get("type") in ("image_url", "output_image"))
        refs.extend(data.get("data") or [])
        results = []
        for item in refs[:MAX_IMAGES]:
            if isinstance(item, str):
                item = {"url": item}
            if not isinstance(item, dict):
                continue
            url_value = item.get("url") or item.get("b64_json") or item.get("image_url") or item.get("image")
            if isinstance(url_value, dict):
                url_value = url_value.get("url")
            if not isinstance(url_value, str):
                continue
            raw = _data_url(url_value)
            if raw is None and item.get("b64_json") and not item.get("url"):
                try:
                    raw = base64.b64decode(url_value, validate=True)
                except (ValueError, binascii.Error):
                    raw = None
            if raw is None:
                if not _safe_remote(url_value):
                    continue
                try:
                    remote = await self.router.client.get(url_value, timeout=60)
                    remote.raise_for_status()
                    raw = remote.content
                except httpx.HTTPError:
                    continue
            mime, width, height = _sniff_image(raw)
            results.append(ImageResult(raw, mime, width, height, str(item.get("revised_prompt") or "")))
        return results, data.get("usage") if isinstance(data.get("usage"), dict) else {}


class GenerateBody(BaseModel):
    prompt: str
    model: str = "vellur-image"
    size: str = "1024x1024"
    n: int = 1
    confirm: bool = False
    conversation_id: Optional[int] = None
    task_id: Optional[int] = None
    max_usd: Optional[float] = None


class EstimateBody(BaseModel):
    prompt: str = ""
    model: str = "vellur-image"
    size: str = "1024x1024"
    n: int = 1
    max_usd: Optional[float] = None


def register(app: FastAPI, ctrl: Controller, router: Router, service: ImageService, admin, admin_write) -> None:
    def err(e: ImageError) -> JSONResponse:
        return JSONResponse(status_code=e.status, content={"error": e.code, "message": e.message, **e.data})

    @app.get("/autopilot/api/images/status", dependencies=[Depends(admin)])
    async def image_status():
        return service.status()

    @app.post("/autopilot/api/images/estimate", dependencies=[Depends(admin_write)])
    async def estimate(body: EstimateBody):
        try:
            return service.estimate(body.prompt, body.model, body.size, body.n, body.max_usd)
        except ImageError as e:
            return err(e)

    @app.post("/autopilot/api/images/generate", dependencies=[Depends(admin_write)])
    async def generate(body: GenerateBody):
        if body.conversation_id and not ctrl.db.one("SELECT id FROM conversations WHERE id=?", (body.conversation_id,)):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown conversation"})
        if body.task_id and not ctrl.db.one("SELECT id FROM tasks WHERE id=?", (body.task_id,)):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown task"})
        try:
            result = await service.generate(body.prompt, body.model, body.size, body.n, body.confirm,
                                            f"image:chat:{body.conversation_id}" if body.conversation_id else "image",
                                            conv_id=body.conversation_id, task_id=body.task_id,
                                            max_usd=body.max_usd)
        except ImageError as e:
            return err(e)
        if body.conversation_id and ctrl.db.one("SELECT id FROM conversations WHERE id=?", (body.conversation_id,)):
            ctrl.db.x("INSERT INTO messages(conv_id,role,content,created,meta) VALUES(?,?,?,?,?)",
                      (body.conversation_id, "assistant", "Generated image", time.time(),
                       json.dumps({"images": result["images"], "model": "vellur-image",
                                   "target": result["target"], "cost_usd": result["cost_usd"],
                                   "cost_source": result["cost_source"]})))
            ctrl.db.x("UPDATE conversations SET updated=? WHERE id=?", (time.time(), body.conversation_id))
        return result

    @app.get("/autopilot/api/images", dependencies=[Depends(admin)])
    async def listing(conv_id: Optional[int] = None, task_id: Optional[int] = None, limit: int = 100):
        where, args = [], []
        if conv_id is not None:
            where.append("conv_id=?"); args.append(conv_id)
        if task_id is not None:
            where.append("task_id=?"); args.append(task_id)
        sql = "SELECT * FROM generated_images" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY created DESC,id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        return {"images": [service.store.public(r) for r in ctrl.db.all(sql, tuple(args))]}

    @app.get("/autopilot/api/images/{iid}", dependencies=[Depends(admin)])
    async def info(iid: str):
        row = service.store.get(iid)
        if not row:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown image"})
        return service.store.public(row)

    @app.get("/autopilot/api/images/{iid}/content", dependencies=[Depends(admin)])
    async def content(iid: str):
        row = service.store.get(iid)
        if not row:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "unknown image"})
        try:
            p = service.store.path(row)
            if not os.path.isfile(p):
                raise OSError("missing")
        except (OSError, ValueError):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "image file is missing"})
        return FileResponse(p, media_type=row["mime"], headers={
            "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline", "Cross-Origin-Resource-Policy": "same-origin"})
