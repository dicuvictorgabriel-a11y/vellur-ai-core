"""Inference endpoint (internal port 8000; network aliases "gpu-backend" and "autopilot").

LiteLLM and agent tasks call it. Every request goes through the model router (app/router.py), which picks a model
API or the GPU backend, streams the answer and records usage. Callers must present the internal key (LiteLLM) or a
task token (agents); the agent network never sees provider keys.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from .lifecycle import Controller
from .router import Router, error_response


def to_fastapi(resp):
    if resp.stream is not None:
        return StreamingResponse(resp.stream, status_code=resp.status, headers=resp.headers, media_type=resp.media_type)
    return Response(resp.content or b"", status_code=resp.status, headers=resp.headers, media_type=resp.media_type)


def build_proxy(ctrl: Controller, transport=None, router: Router | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    rt = router or Router(ctrl, transport)
    app.state.router = rt

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request):
        bearer = request.headers.get("authorization", "")
        bearer = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
        tag = rt.authorize(bearer)
        if tag is None:
            return to_fastapi(error_response("unauthorized", "missing or invalid internal key", 401))
        body = await request.body()
        resp = await rt.handle(request.method, path, request.url.query, dict(request.headers), body, tag)
        return to_fastapi(resp)

    return app
