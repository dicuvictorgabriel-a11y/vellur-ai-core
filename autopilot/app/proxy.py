"""Inference proxy (internal port 8000, network alias "gpu-backend"). LiteLLM's vellur-coder points here.

Every request: ensure a GPU is ready (start it if policy allows, hold the request while it boots), then stream it to
the GPU backend while counting it as activity. Errors come back as OpenAI-style JSON with a precise reason.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .lifecycle import ControlError, Controller

HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "te", "upgrade",
       "proxy-authorization", "proxy-connection"}

REASON_HELP = {
    "budget": "budget", "budget_exhausted": "budget", "budget_insufficient": "budget", "price": "budget",
    "price_above_limit": "budget", "capacity": "capacity", "auth": "authentication", "config": "configuration",
    "manual": "manual action needed", "auto_start_disabled": "configuration", "disabled": "configuration",
    "starting": "starting", "timeout": "provider", "provider": "provider", "dry_run": "dry-run",
    "session_limit": "session limit", "busy": "busy", "termination_unverified": "safety",
}


def error(code: str, message: str, status: int = 503) -> JSONResponse:
    category = REASON_HELP.get(code, "provider")
    headers = {"Retry-After": "60"} if code in ("starting", "busy") else {}
    return JSONResponse(status_code=status, headers=headers, content={"error": {
        "message": f"VELLUR GPU Autopilot ({category}): {message}", "type": "vellur_autopilot", "code": code}})


def build_proxy(ctrl: Controller, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(connect=15, read=900, write=120, pool=30))

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request):
        try:
            base = await ctrl.ensure_ready()
        except ControlError as e:
            return error(e.code, e.message)
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        url = f"{base}/v1/{path}"
        if request.url.query:
            url += "?" + request.url.query
        scope = ctrl.activity()
        await scope.__aenter__()
        try:
            req = client.build_request(request.method, url, content=body, headers=headers)
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as e:
            await scope.__aexit__(None, None, None)
            return error("provider", f"the GPU backend did not answer ({e.__class__.__name__})", 502)

        async def relay():
            try:
                async for chunk in upstream.aiter_bytes():   # decoded; content-encoding header is dropped below
                    yield chunk
            finally:
                await upstream.aclose()
                await scope.__aexit__(None, None, None)

        out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP | {"content-encoding"}}
        return StreamingResponse(relay(), status_code=upstream.status_code, headers=out_headers,
                                 media_type=upstream.headers.get("content-type"))

    return app
