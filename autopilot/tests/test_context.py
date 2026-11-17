"""Tests for NOVA Context Bridge."""

from __future__ import annotations

import json
import tempfile
import time

import pytest

from app.context import ContextService, get_context_service
from app.db import DB
from app.mcp_adapter import MCPServer, check_scope, SCOPE_TO_SOURCES


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = DB(path)
    yield db
    import os
    os.unlink(path)


@pytest.fixture
def context_service(db):
    return ContextService(db)


class TestContextService:
    def test_schema_creation(self, context_service):
        """Test that all context tables are created."""
        tables = context_service.db.all("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = {t["name"] for t in tables}
        assert "context_items" in table_names
        assert "context_fts" in table_names
        assert "context_refresh_log" in table_names
        assert "mcp_tokens" in table_names

    def test_upsert_and_get(self, context_service):
        """Test basic CRUD operations."""
        from app.context import ContextItem
        item = ContextItem(
            id=None,
            project="test-project",
            source="timeline",
            type="task",
            ref_id="task-1",
            title="Test Task",
            summary="A test task",
            content=json.dumps({"detail": "full content"}),
            metadata=json.dumps({"cost": 0.01}),
            tags="task,test",
            created_at=time.time(),
            updated_at=time.time(),
            expires_at=None,
        )
        item_id = context_service.upsert(item)
        assert item_id > 0
        assert item.id == item_id

        retrieved = context_service.get("test-project", "timeline", "task", "task-1")
        assert retrieved is not None
        assert retrieved.title == "Test Task"
        assert retrieved.summary == "A test task"
        assert retrieved.metadata is not None
        meta = json.loads(retrieved.metadata)
        assert meta["cost"] == 0.01

    def test_query_by_source(self, context_service):
        """Test querying by source."""
        from app.context import ContextItem
        now = time.time()
        for i in range(3):
            context_service.upsert(ContextItem(
                id=None, project="p1", source="timeline", type="event", ref_id=f"e{i}",
                title=f"Event {i}", summary=None, content="{}", metadata="{}", tags=None,
                created_at=now, updated_at=now, expires_at=None
            ))
        for i in range(2):
            context_service.upsert(ContextItem(
                id=None, project="p1", source="knowledge", type="entry", ref_id=f"k{i}",
                title=f"Knowledge {i}", summary=None, content="{}", metadata="{}", tags=None,
                created_at=now, updated_at=now, expires_at=None
            ))

        timeline = context_service.query("p1", source="timeline")
        assert len(timeline) == 3
        knowledge = context_service.query("p1", source="knowledge")
        assert len(knowledge) == 2

    def test_query_by_type(self, context_service):
        """Test querying by type."""
        from app.context import ContextItem
        now = time.time()
        context_service.upsert(ContextItem(
            id=None, project="p1", source="agent", type="task", ref_id="t1",
            title="Task 1", summary=None, content="{}", metadata="{}", tags=None,
            created_at=now, updated_at=now, expires_at=None
        ))
        context_service.upsert(ContextItem(
            id=None, project="p1", source="agent", type="run", ref_id="r1",
            title="Run 1", summary=None, content="{}", metadata="{}", tags=None,
            created_at=now, updated_at=now, expires_at=None
        ))

        tasks = context_service.query("p1", source="agent", type="task")
        assert len(tasks) == 1
        assert tasks[0].type == "task"

        runs = context_service.query("p1", source="agent", type="run")
        assert len(runs) == 1
        assert runs[0].type == "run"

    def test_search_fts(self, context_service):
        """Test full-text search."""
        from app.context import ContextItem
        now = time.time()
        context_service.upsert(ContextItem(
            id=None, project="p1", source="knowledge", type="entry", ref_id="k1",
            title="Python async patterns", summary="async/await guide", content="Use asyncio",
            metadata="{}", tags="python,async", created_at=now, updated_at=now, expires_at=None
        ))
        context_service.upsert(ContextItem(
            id=None, project="p1", source="knowledge", type="entry", ref_id="k2",
            title="JavaScript promises", summary="Promise guide", content="Use Promise.all",
            metadata="{}", tags="js,promise", created_at=now, updated_at=now, expires_at=None
        ))

        # Search should find the Python entry
        results = context_service.search("p1", "Python")
        assert len(results) == 1
        assert "Python" in results[0].title

        # Search should find both via content
        results = context_service.search("p1", "guide")
        assert len(results) == 2

    def test_search_fallback_like(self, context_service):
        """Test LIKE fallback when FTS5 unavailable."""
        from app.context import ContextItem
        now = time.time()
        context_service.upsert(ContextItem(
            id=None, project="p1", source="timeline", type="event", ref_id="e1",
            title="Deployment to production", summary="deployed v1.2.3", content="{}",
            metadata="{}", tags="deploy,prod", created_at=now, updated_at=now, expires_at=None
        ))

        results = context_service.search("p1", "production")
        assert len(results) == 1
        assert "production" in results[0].title

    def test_refresh_timeline(self, context_service, db):
        """Test refreshing from timeline."""
        # Add some timeline events using correct schema (event_id is required)
        now = time.time()
        db.x("""INSERT INTO timeline (event_id, project, event_type, category, title, payload, occurred_at, observed_at, source, correlation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
             ("evt-1", "test-proj", "deployment", "deploy,prod", "Deployed v1.0", '{"ref": "deploy-1"}', now, now, "system", "corr-1"))
        db.x("""INSERT INTO timeline (event_id, project, event_type, category, title, payload, occurred_at, observed_at, source, correlation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
             ("evt-2", "test-proj", "failure", "fail,build", "Build failed", '{"ref": "fail-1"}', now, now, "system", "corr-2"))

        result = context_service.refresh_source("test-proj", "timeline", "manual")
        assert result.items_added == 2
        assert result.items_updated == 0
        assert result.error is None

        # Refresh again - should update not add
        result2 = context_service.refresh_source("test-proj", "timeline", "manual")
        assert result2.items_added == 0
        assert result2.items_updated == 2

    def test_refresh_knowledge(self, context_service, db):
        """Test refreshing from knowledge."""
        now = time.time()
        db.x("""INSERT INTO knowledge (project, namespace, title, body, status, source, tags, paths, created, updated, version, locked)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
             ("test-proj", "default", "K1", "Content 1", "implemented", "manual", "tag1", "[]", now, now, 1, 0))
        db.x("""INSERT INTO knowledge (project, namespace, title, body, status, source, tags, paths, created, updated, version, locked)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
             ("test-proj", "default", "K2", "Content 2", "implemented", "manual", "tag2", "[]", now, now, 1, 0))

        result = context_service.refresh_source("test-proj", "knowledge", "manual")
        assert result.items_added == 2

    def test_refresh_all(self, context_service, db):
        """Test refreshing all sources."""
        now = time.time()
        db.x("""INSERT INTO timeline (event_id, project, event_type, category, title, payload, occurred_at, observed_at, source, correlation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
             ("evt-1", "test-proj", "event", "general", "Event 1", "{}", now, now, "system", "corr-1"))
        db.x("""INSERT INTO knowledge (project, namespace, title, body, status, source, tags, paths, created, updated, version, locked)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
             ("test-proj", "default", "K1", "C1", "implemented", "manual", "t1", "[]", now, now, 1, 0))

        results = context_service.refresh_all("test-proj", "manual")
        assert len(results) == 6  # timeline, knowledge, agent, controller, health, screenshot (no release)
        total_added = sum(r.items_added for r in results)
        assert total_added >= 2

    def test_get_agent_context(self, context_service, db):
        """Test getting bounded context for agent injection."""
        now = time.time()
        # Add knowledge
        db.x("""INSERT INTO knowledge (project, namespace, title, body, status, source, tags, paths, created, updated, version, locked)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
             ("test-proj", "default", "Important Knowledge", "Key info\nDetails here", "implemented", "manual", "important", "[]", now, now, 1, 0))
        # Add task
        db.x("""INSERT INTO tasks (created, updated, state, repo, base, model, prompt, outcome, runs)
                VALUES (?,?,?,?,?,?,?,?,?)""",
             (now, now, "running", "test-proj", "", "model", "Do something", "", 1))
        # Add task events for run
        task_row = db.one("SELECT id FROM tasks WHERE repo=?", ("test-proj",))
        task_id = task_row["id"]
        db.x("INSERT INTO task_events (task_id, ts, run, kind, data) VALUES (?,?,?,?,?)",
             (task_id, now, 1, "started", '{}'))
        db.x("INSERT INTO task_events (task_id, ts, run, kind, data) VALUES (?,?,?,?,?)",
             (task_id, now, 1, "completed", '{}'))
        # Add session
        db.x("""INSERT INTO sessions (provider, profile, trigger, state, reason, instance_id, offer, gpu_name, vram_gb, dph, extra_cost, created_at, ready_at, billing_started_at, ended_at, termination_verified, confirmed_cost, reconciled, dry_run)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             ("mock", "coder", "manual", "ready", "", "", "{}", "Mock GPU", 24, 0.5, 0, now, now, now, None, 0, 0, 0, 0))
        # Add health boot
        db.kv_set("controller_boots", [{"ts": now, "clean": True, "memory": {"rss_mb": 100}}])

        # First refresh to populate context
        context_service.refresh_all("test-proj", "manual")

        context = context_service.get_agent_context("test-proj", task_id=str(task_id), max_items=10, max_tokens=4000)
        assert context["project"] == "test-proj"
        assert context["task_id"] == str(task_id)
        assert len(context["knowledge"]) == 1
        assert len(context["recent_tasks"]) == 1
        assert len(context["recent_runs"]) == 1
        assert context["health_status"] == "recently_restarted"  # only boots, no heartbeat

    def test_delete(self, context_service):
        """Test deleting context items."""
        from app.context import ContextItem
        now = time.time()
        context_service.upsert(ContextItem(
            id=None, project="p1", source="timeline", type="event", ref_id="e1",
            title="Event", summary=None, content="{}", metadata="{}", tags=None,
            created_at=now, updated_at=now, expires_at=None
        ))
        assert context_service.get("p1", "timeline", "event", "e1") is not None
        deleted = context_service.delete("p1", "timeline", "event", "e1")
        assert deleted
        assert context_service.get("p1", "timeline", "event", "e1") is None

    def test_refresh_log(self, context_service):
        """Test refresh log recording."""
        result = context_service.refresh_source("test-proj", "timeline", "manual")
        assert result.duration_ms >= 0

        logs = context_service.db.all("SELECT * FROM context_refresh_log WHERE project=?", ("test-proj",))
        assert len(logs) == 1
        assert logs[0]["source"] == "timeline"
        assert logs[0]["triggered_by"] == "manual"


class TestMCPTokenManagement:
    def test_create_and_verify_token(self, context_service):
        """Test MCP token creation and verification."""
        token = context_service.create_mcp_token("test-client", ["read:context", "read:knowledge"])
        assert len(token) > 20

        client = context_service.verify_mcp_token(token)
        assert client is not None
        assert client["client_name"] == "test-client"
        assert "read:context" in client["scopes"]
        assert "read:knowledge" in client["scopes"]

    def test_revoke_token(self, context_service):
        """Test token revocation."""
        token = context_service.create_mcp_token("revoke-test", ["read:context"])
        assert context_service.verify_mcp_token(token) is not None

        revoked = context_service.revoke_mcp_token("revoke-test")
        assert revoked
        assert context_service.verify_mcp_token(token) is None

    def test_list_tokens(self, context_service):
        """Test listing tokens."""
        context_service.create_mcp_token("client1", ["read:context"])
        context_service.create_mcp_token("client2", ["read:knowledge"])

        tokens = context_service.list_mcp_tokens()
        assert len(tokens) == 2
        names = {t["client_name"] for t in tokens}
        assert names == {"client1", "client2"}

    def test_verify_invalid_token(self, context_service):
        """Test verifying invalid token."""
        assert context_service.verify_mcp_token("invalid-token") is None


class TestScopeChecking:
    def test_check_scope_read_context(self):
        """Test read:context scope grants all sources."""
        assert check_scope(["read:context"], "timeline")
        assert check_scope(["read:context"], "knowledge")
        assert check_scope(["read:context"], "agent")
        assert check_scope(["read:context"], "controller")
        assert check_scope(["read:context"], "health")
        assert check_scope(["read:context"], "screenshot")

    def test_check_scope_specific(self):
        """Test specific scopes grant only their sources."""
        assert check_scope(["read:knowledge"], "knowledge")
        assert not check_scope(["read:knowledge"], "timeline")
        assert not check_scope(["read:knowledge"], "agent")

        assert check_scope(["read:timeline"], "timeline")
        assert not check_scope(["read:timeline"], "knowledge")

        assert check_scope(["read:agent"], "agent")
        assert check_scope(["read:controller"], "controller")
        assert check_scope(["read:controller"], "health")
        assert check_scope(["read:health"], "health")
        assert check_scope(["read:screenshot"], "screenshot")

    def test_check_scope_multiple(self):
        """Test multiple scopes."""
        assert check_scope(["read:knowledge", "read:timeline"], "knowledge")
        assert check_scope(["read:knowledge", "read:timeline"], "timeline")
        assert not check_scope(["read:knowledge", "read:timeline"], "agent")


class TestMCPServer:
    @pytest.fixture
    def mcp_server(self, context_service):
        return MCPServer(context_service)

    @pytest.mark.asyncio
    async def test_initialize(self, mcp_server):
        """Test MCP initialize request."""
        from app.mcp_adapter import MCPRequest
        req = MCPRequest(method="initialize", id="1")
        resp = await mcp_server.handle_request(req, ["read:context"])
        assert resp.id == "1"
        assert resp.result is not None
        assert resp.result["protocolVersion"] == "2024-11-05"
        assert resp.result["serverInfo"]["name"] == "nova-context-bridge"

    @pytest.mark.asyncio
    async def test_tools_list(self, mcp_server):
        """Test MCP tools/list request."""
        from app.mcp_adapter import MCPRequest
        req = MCPRequest(method="tools/list", id="1")
        resp = await mcp_server.handle_request(req, ["read:context"])
        assert resp.id == "1"
        assert "tools" in resp.result
        assert len(resp.result["tools"]) == 8  # all tools available with read:context

    @pytest.mark.asyncio
    async def test_tools_list_limited_scope(self, mcp_server):
        """Test tools/list with limited scope."""
        from app.mcp_adapter import MCPRequest
        req = MCPRequest(method="tools/list", id="1")
        resp = await mcp_server.handle_request(req, ["read:knowledge"])
        assert resp.id == "1"
        # Only knowledge tools should be available
        tool_names = {t["name"] for t in resp.result["tools"]}
        assert "get_knowledge_context" in tool_names
        assert "get_task_context" not in tool_names  # needs read:agent

    @pytest.mark.asyncio
    async def test_ping(self, mcp_server):
        """Test MCP ping."""
        from app.mcp_adapter import MCPRequest
        req = MCPRequest(method="ping", id="1")
        resp = await mcp_server.handle_request(req, ["read:context"])
        assert resp.id == "1"
        assert resp.result == {}

    @pytest.mark.asyncio
    async def test_tool_call_get_project_context(self, mcp_server, context_service, db):
        """Test get_project_context tool."""
        from app.mcp_adapter import MCPRequest
        now = time.time()
        db.x("""INSERT INTO knowledge (project, namespace, title, body, status, source, tags, paths, created, updated, version, locked)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
             ("test-proj", "default", "K1", "C1", "implemented", "manual", "t1", "[]", now, now, 1, 0))

        # Refresh to populate context
        context_service.refresh_all("test-proj", "manual")

        req = MCPRequest(
            method="tools/call",
            id="1",
            params={"name": "get_project_context", "arguments": {"project": "test-proj"}}
        )
        resp = await mcp_server.handle_request(req, ["read:context"])
        assert resp.id == "1"
        assert resp.error is None
        content = json.loads(resp.result["content"][0]["text"])
        assert content["project"] == "test-proj"
        assert len(content["knowledge"]) == 1

    @pytest.mark.asyncio
    async def test_tool_call_insufficient_scope(self, mcp_server):
        """Test tool call with insufficient scope."""
        from app.mcp_adapter import MCPRequest
        req = MCPRequest(
            method="tools/call",
            id="1",
            params={"name": "get_task_context", "arguments": {"project": "p", "task_id": "1"}}
        )
        resp = await mcp_server.handle_request(req, ["read:knowledge"])  # missing read:agent
        assert resp.id == "1"
        assert resp.error is not None
        assert "Insufficient scope" in resp.error["message"]

    @pytest.mark.asyncio
    async def test_tool_call_unknown_tool(self, mcp_server):
        """Test calling unknown tool."""
        from app.mcp_adapter import MCPRequest
        req = MCPRequest(
            method="tools/call",
            id="1",
            params={"name": "unknown_tool", "arguments": {}}
        )
        resp = await mcp_server.handle_request(req, ["read:context"])
        assert resp.id == "1"
        assert resp.error is not None
        assert "Unknown tool" in resp.error["message"]


class TestRestartRecovery:
    def test_on_controller_start(self, context_service, db):
        """Test context recovery on controller start."""
        now = time.time()
        db.x("""INSERT INTO timeline (event_id, project, event_type, category, title, payload, occurred_at, observed_at, source, correlation_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
             ("evt-1", "test-proj", "event", "general", "Event 1", "{}", now, now, "system", "corr-1"))

        result = context_service.on_controller_start("test-proj")
        assert "refresh_results" in result
        assert len(result["refresh_results"]) == 6  # timeline, knowledge, agent, controller, health, screenshot

        # Check restart recovery item was created
        recovery = context_service.query("test-proj", source="health", type="restart_recovery")
        assert len(recovery) == 1
        assert "Context Recovered" in recovery[0].title


class TestScreenshotMetadata:
    def test_refresh_screenshot(self, context_service, db):
        """Test screenshot metadata refresh."""
        now = time.time()
        # Create a task first
        db.x("""INSERT INTO tasks (created, updated, state, repo, base, model, prompt, outcome, runs)
                VALUES (?,?,?,?,?,?,?,?,?)""",
             (now, now, "running", "test-proj", "", "model", "Test task", "", 1))
        task_row = db.one("SELECT id FROM tasks WHERE repo=?", ("test-proj",))
        task_id = task_row["id"]
        
        # Add screenshot event
        db.x("INSERT INTO task_events (task_id, ts, run, kind, data) VALUES (?,?,?,?,?)",
             (task_id, now, 1, "shot",
              json.dumps({"name": "screenshot-1", "description": "Test shot", "viewport": "desktop", "url": "/shots/1.png"})))

        result = context_service.refresh_source("test-proj", "screenshot", "manual")
        assert result.items_added == 1

        shots = context_service.query("test-proj", source="screenshot", type="screenshot_meta")
        assert len(shots) == 1
        assert shots[0].title == "Screenshot: screenshot-1"
        meta = json.loads(shots[0].content)
        assert meta["viewport"] == "desktop"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])