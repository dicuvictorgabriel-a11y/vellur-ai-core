"""NOVA Context Bridge MCP Adapter: read-only MCP tools for external agents.

Exposes context via Model Context Protocol (MCP) with strict read-only access.
Authorization via bearer tokens (hashed in DB). Scopes control access per source.

Transport: HTTP/JSON-RPC (streamable HTTP) on /autopilot/api/mcp
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .context import ContextService
from .db import DB


# ---- MCP Protocol Models ----

class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[str | int] = None
    method: str
    params: Optional[dict] = None


class MCPResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[str | int] = None
    result: Optional[Any] = None
    error: Optional[dict] = None


class MCPNotification(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[dict] = None


# ---- Tool Definitions (READ-ONLY) ----

MCP_TOOLS = [
    {
        "name": "get_project_context",
        "description": "Get bounded, relevant context for a project (knowledge, recent tasks, runs, decisions, sessions, release, health, screenshots). No paid inference.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project/repository name"},
                "task_id": {"type": "string", "description": "Optional task ID to focus context"},
                "max_items": {"type": "integer", "default": 50, "description": "Max context items"},
                "max_tokens": {"type": "integer", "default": 8000, "description": "Approximate token budget"},
            },
            "required": ["project"],
        },
    },
    {
        "name": "get_task_context",
        "description": "Get detailed context for a specific agent task (events, runs, reports, files changed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "task_id": {"type": "string"},
                "include_events": {"type": "boolean", "default": True},
                "include_runs": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["project", "task_id"],
        },
    },
    {
        "name": "get_release_context",
        "description": "Get release/deployment context for a project (versions, delivery records, GitHub/installer reports).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "version": {"type": "string", "description": "Optional specific version"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["project"],
        },
    },
    {
        "name": "get_knowledge_context",
        "description": "Search project knowledge (durable memory) via full-text search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "query": {"type": "string", "description": "Search query (FTS5 syntax)"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["project"],
        },
    },
    {
        "name": "get_screenshot_metadata",
        "description": "Get screenshot metadata (no file access) for a project or task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "task_id": {"type": "string", "description": "Optional task filter"},
                "run": {"type": "string", "description": "Optional run filter"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["project"],
        },
    },
    {
        "name": "search_context",
        "description": "Full-text search across all context sources for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "query": {"type": "string", "description": "Search query"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Optional source filter"},
                "types": {"type": "array", "items": {"type": "string"}, "description": "Optional type filter"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["project"],
        },
    },
    {
        "name": "get_controller_health",
        "description": "Get controller health status (boots, heartbeats, memory, OOM).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
            },
            "required": ["project"],
        },
    },
    {
        "name": "get_refresh_log",
        "description": "Get context refresh audit log for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "source": {"type": "string", "description": "Optional source filter"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["project"],
        },
    },
]


# ---- Scope Definitions ----

SCOPE_TO_SOURCES = {
    "read:context": ["timeline", "knowledge", "agent", "controller", "health", "screenshot", "release"],
    "read:knowledge": ["knowledge"],
    "read:timeline": ["timeline"],
    "read:agent": ["agent"],
    "read:controller": ["controller", "health"],
    "read:health": ["health"],
    "read:screenshot": ["screenshot"],
    "read:release": ["release"],
}


def check_scope(scopes: list[str], required_source: str) -> bool:
    """Check if any scope grants access to the required source."""
    for scope in scopes:
        if scope in SCOPE_TO_SOURCES and required_source in SCOPE_TO_SOURCES[scope]:
            return True
        if scope == "read:context":
            return True
    return False


# ---- MCP Server Implementation ----

class MCPServer:
    def __init__(self, context_service: ContextService):
        self.context = context_service
        self.tools = {t["name"]: t for t in MCP_TOOLS}
        self.handlers: dict[str, Callable] = {
            "get_project_context": self._handle_get_project_context,
            "get_task_context": self._handle_get_task_context,
            "get_release_context": self._handle_get_release_context,
            "get_knowledge_context": self._handle_get_knowledge_context,
            "get_screenshot_metadata": self._handle_get_screenshot_metadata,
            "search_context": self._handle_search_context,
            "get_controller_health": self._handle_get_controller_health,
            "get_refresh_log": self._handle_get_refresh_log,
        }

    async def handle_request(self, request: MCPRequest, scopes: list[str]) -> MCPResponse:
        if request.method == "initialize":
            return MCPResponse(
                id=request.id,
                result={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "nova-context-bridge", "version": "1.0.0"},
                },
            )

        if request.method == "tools/list":
            # Filter tools by scopes
            available = []
            for tool in MCP_TOOLS:
                required_sources = self._tool_required_sources(tool["name"])
                if all(check_scope(scopes, src) for src in required_sources):
                    available.append(tool)
            return MCPResponse(id=request.id, result={"tools": available})

        if request.method == "tools/call":
            return await self._handle_tool_call(request, scopes)

        if request.method == "ping":
            return MCPResponse(id=request.id, result={})

        return MCPResponse(
            id=request.id,
            error={"code": -32601, "message": f"Method not found: {request.method}"},
        )

    def _tool_required_sources(self, tool_name: str) -> list[str]:
        mapping = {
            "get_project_context": ["timeline", "knowledge", "agent", "controller", "release", "health", "screenshot"],
            "get_task_context": ["agent", "timeline"],
            "get_release_context": ["release"],
            "get_knowledge_context": ["knowledge"],
            "get_screenshot_metadata": ["screenshot"],
            "search_context": ["timeline", "knowledge", "agent", "controller", "release", "health", "screenshot"],
            "get_controller_health": ["health", "controller"],
            "get_refresh_log": ["timeline", "knowledge", "agent", "controller", "release", "health", "screenshot"],
        }
        return mapping.get(tool_name, [])

    async def _handle_tool_call(self, request: MCPRequest, scopes: list[str]) -> MCPResponse:
        params = request.params or {}
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if not tool_name:
            return MCPResponse(
                id=request.id,
                error={"code": -32602, "message": "Missing tool name"},
            )

        if tool_name not in self.handlers:
            return MCPResponse(
                id=request.id,
                error={"code": -32601, "message": f"Unknown tool: {tool_name}"},
            )

        # Check scopes for this tool
        required_sources = self._tool_required_sources(tool_name)
        for src in required_sources:
            if not check_scope(scopes, src):
                return MCPResponse(
                    id=request.id,
                    error={"code": -32603, "message": f"Insufficient scope for {tool_name}: need access to {src}"},
                )

        try:
            result = await self.handlers[tool_name](arguments)
            return MCPResponse(id=request.id, result={"content": [{"type": "text", "text": json.dumps(result, indent=2)}]})
        except Exception as e:
            return MCPResponse(
                id=request.id,
                error={"code": -32603, "message": f"Tool execution failed: {str(e)}"},
            )

    # ---- Tool Handlers ----

    async def _handle_get_project_context(self, args: dict) -> dict:
        project = args["project"]
        task_id = args.get("task_id")
        max_items = args.get("max_items", 50)
        max_tokens = args.get("max_tokens", 8000)
        return self.context.get_agent_context(project, task_id, max_items, max_tokens)

    async def _handle_get_task_context(self, args: dict) -> dict:
        project = args["project"]
        task_id = args["task_id"]
        include_events = args.get("include_events", True)
        include_runs = args.get("include_runs", True)
        limit = args.get("limit", 100)

        result = {"project": project, "task_id": task_id, "task": None, "events": [], "runs": [], "reports": []}

        # Get task
        task_item = self.context.get(project, "agent", "task", task_id)
        if task_item:
            result["task"] = task_item.to_dict()

        # Get events from timeline
        if include_events:
            events = self.context.query(project, source="timeline", limit=limit)
            task_events = [e for e in events if e.metadata and task_id in e.metadata]
            result["events"] = [e.to_dict() for e in task_events]

        # Get runs
        if include_runs:
            runs = self.context.query(project, source="agent", type="run", limit=limit)
            task_runs = [r for r in runs if r.ref_id and r.ref_id.startswith(f"{task_id}:")]
            result["runs"] = [r.to_dict() for r in task_runs]

        return result

    async def _handle_get_release_context(self, args: dict) -> dict:
        project = args["project"]
        version = args.get("version")
        limit = args.get("limit", 10)

        if version:
            releases = self.context.query(project, source="release", type="release", limit=limit)
            releases = [r for r in releases if r.metadata and version in (r.metadata.get("version") or "")]
        else:
            releases = self.context.query(project, source="release", type="release", limit=limit)

        return {"project": project, "releases": [r.to_dict() for r in releases]}

    async def _handle_get_knowledge_context(self, args: dict) -> dict:
        project = args["project"]
        query = args.get("query", "")
        limit = args.get("limit", 20)

        if query:
            results = self.context.search(project, query, limit)
        else:
            results = self.context.query(project, source="knowledge", type="knowledge_entry", limit=limit)

        return {"project": project, "knowledge": [r.to_dict() for r in results]}

    async def _handle_get_screenshot_metadata(self, args: dict) -> dict:
        project = args["project"]
        task_id = args.get("task_id")
        run = args.get("run")
        limit = args.get("limit", 20)

        shots = self.context.query(project, source="screenshot", type="screenshot_meta", limit=limit)
        if task_id:
            shots = [s for s in shots if s.metadata and task_id in s.metadata]
        if run:
            shots = [s for s in shots if s.metadata and run in s.metadata]

        return {"project": project, "screenshots": [s.to_dict() for s in shots]}

    async def _handle_search_context(self, args: dict) -> dict:
        project = args["project"]
        query = args["query"]
        sources = args.get("sources")
        types = args.get("types")
        limit = args.get("limit", 50)

        results = self.context.search(project, query, limit)

        if sources:
            results = [r for r in results if r.source in sources]
        if types:
            results = [r for r in results if r.type in types]

        return {"project": project, "results": [r.to_dict() for r in results]}

    async def _handle_get_controller_health(self, args: dict) -> dict:
        project = args["project"]
        health = self.context.query(project, source="health", limit=20)
        return {"project": project, "health": [h.to_dict() for h in health]}

    async def _handle_get_refresh_log(self, args: dict) -> dict:
        project = args["project"]
        source = args.get("source")
        limit = args.get("limit", 50)

        sql = "SELECT * FROM context_refresh_log WHERE project=?"
        params = [project]
        if source:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self.context.db.all(sql, tuple(params))
        return {"project": project, "refresh_log": [dict(r) for r in rows]}


# ---- FastAPI Integration ----

_mcp_server: Optional[MCPServer] = None


async def verify_mcp_token(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Verify MCP bearer token and return client scopes."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    context_service: ContextService = request.app.state.context_service
    token = authorization[7:]  # Remove "Bearer "
    client = context_service.verify_mcp_token(token)
    if not client:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")

    return client


async def get_mcp_server(request: Request) -> MCPServer:
    """Get or create MCP server from app state."""
    if not hasattr(request.app.state, "_mcp_server"):
        context_service: ContextService = request.app.state.context_service
        request.app.state._mcp_server = MCPServer(context_service)
    return request.app.state._mcp_server


def register_mcp_routes(app: FastAPI, context_service: ContextService, admin_write: Callable) -> None:
    """Register MCP endpoints on the FastAPI app."""
    # Store context_service in app state for dependencies
    app.state.context_service = context_service

    @app.post("/autopilot/api/mcp")
    async def mcp_endpoint(
        request: Request,
        mcp_request: MCPRequest,
        client: dict = Depends(verify_mcp_token),
        mcp_server: MCPServer = Depends(get_mcp_server),
    ):
        """MCP JSON-RPC endpoint (streamable HTTP)."""
        response = await mcp_server.handle_request(mcp_request, client["scopes"])
        return JSONResponse(content=response.model_dump(exclude_none=True))

    @app.get("/autopilot/api/mcp")
    async def mcp_sse(
        request: Request,
        client: dict = Depends(verify_mcp_token),
        mcp_server: MCPServer = Depends(get_mcp_server),
    ):
        """MCP Server-Sent Events endpoint for streaming."""
        async def event_stream():
            # Send initialize response
            init_response = await mcp_server.handle_request(
                MCPRequest(method="initialize", id="init"),
                client["scopes"],
            )
            yield f"data: {init_response.model_dump_json(exclude_none=True)}\n\n"

            # Keep connection alive for notifications (not implemented yet)
            try:
                while True:
                    await request.receive()
            except Exception:
                pass

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/autopilot/api/mcp/tokens", dependencies=[Depends(admin_write)])
    async def create_mcp_token(
        request: Request,
        client_name: str = Body(...),
        scopes: list[str] = Body(...),
    ):
        """Create a new MCP token (admin only via X-Vellur-Admin)."""
        context_service: ContextService = request.app.state.context_service
        token = context_service.create_mcp_token(client_name, scopes)
        return {"client_name": client_name, "token": token, "scopes": scopes}

    @app.get("/autopilot/api/mcp/tokens", dependencies=[Depends(admin_write)])
    async def list_mcp_tokens(request: Request):
        """List MCP tokens (admin only)."""
        context_service: ContextService = request.app.state.context_service
        return {"tokens": context_service.list_mcp_tokens()}

    @app.delete("/autopilot/api/mcp/tokens/{client_name}", dependencies=[Depends(admin_write)])
    async def revoke_mcp_token(
        request: Request,
        client_name: str,
    ):
        """Revoke an MCP token (admin only)."""
        context_service: ContextService = request.app.state.context_service
        success = context_service.revoke_mcp_token(client_name)
        return {"revoked": success}