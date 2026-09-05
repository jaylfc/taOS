from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.mcp.registry import MCPServerStore
from tinyagentos.mcp.supervisor import MCPSupervisor
from tinyagentos.routes.mcp import router as mcp_router
from tinyagentos.secrets import SecretsStore


@pytest_asyncio.fixture
async def app_client(tmp_path):
    """Minimal FastAPI app with only the MCP router wired."""
    from fastapi import FastAPI

    mini_app = FastAPI()
    mini_app.include_router(mcp_router)

    mcp_store = MCPServerStore(tmp_path / "mcp.db")
    await mcp_store.init()
    secrets_store = SecretsStore(tmp_path / "secrets.db")
    await secrets_store.init()
    mcp_supervisor = MCPSupervisor(store=mcp_store, catalog=None, notif_store=None)

    mini_app.state.mcp_store = mcp_store
    mini_app.state.mcp_supervisor = mcp_supervisor
    mini_app.state.secrets = secrets_store
    mini_app.state.registry = None

    transport = ASGITransport(app=mini_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mini_app

    await mcp_supervisor.stop_all()
    await secrets_store.close()
    await mcp_store.close()


@pytest.mark.asyncio
class TestMCPGetServers:
    async def test_list_servers_empty(self, app_client):
        client, app = app_client
        resp = await client.get("/api/mcp/servers")
        assert resp.status_code == 200
        assert resp.json() == {"servers": []}

    async def test_list_servers_returns_registered_servers(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")
        await mcp_store.register_server("mcp-search", "2.0.0", "sse")

        resp = await client.get("/api/mcp/servers")
        assert resp.status_code == 200
        data = resp.json()
        assert "servers" in data
        assert len(data["servers"]) == 2
        ids = {s["id"] for s in data["servers"]}
        assert ids == {"mcp-fetch", "mcp-search"}

    async def test_list_servers_includes_status_fields(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers")
        assert resp.status_code == 200
        server = resp.json()["servers"][0]
        assert server["id"] == "mcp-fetch"
        assert "running" in server
        assert "pid" in server
        assert server["running"] is False


@pytest.mark.asyncio
class TestMCPGetCapabilities:
    async def test_capabilities_happy_path(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers/mcp-fetch/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert "capabilities" in data
        assert isinstance(data["capabilities"], list)

    async def test_capabilities_unknown_server_returns_404(self, app_client):
        client, app = app_client
        resp = await client.get("/api/mcp/servers/unknown-server/capabilities")
        assert resp.status_code == 404
        assert resp.json()["error"] == "server not found"


@pytest.mark.asyncio
class TestMCPGetLogs:
    async def test_logs_happy_path(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers/mcp-fetch/logs?since=0&limit=50")
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data
        assert "count" in data
        assert isinstance(data["logs"], list)
        assert data["count"] == len(data["logs"])

    async def test_logs_invalid_since_returns_422(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers/mcp-fetch/logs?since=abc&limit=10")
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestMCPGetPermissions:
    async def test_permissions_empty_when_no_attachments(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers/mcp-fetch/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert data["attachments"] == []

    async def test_permissions_returns_attachments(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")
        await mcp_store.add_attachment(
            "mcp-fetch", "agent", "bot1",
            allowed_tools=["fetch_url"],
            allowed_resources=["https://*"],
        )

        resp = await client.get("/api/mcp/servers/mcp-fetch/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert len(data["attachments"]) == 1
        att = data["attachments"][0]
        assert att["scope_kind"] == "agent"
        assert att["scope_id"] == "bot1"
        assert att["allowed_tools"] == ["fetch_url"]


@pytest.mark.asyncio
class TestMCPGetConfig:
    async def test_config_returns_empty_for_new_server(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers/mcp-fetch/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert data["config"] == {}

    async def test_config_roundtrip(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        put_resp = await client.put(
            "/api/mcp/servers/mcp-fetch/config",
            json={"config": {"timeout": 60, "max_retries": 3}},
        )
        assert put_resp.status_code == 200

        get_resp = await client.get("/api/mcp/servers/mcp-fetch/config")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["config"]["timeout"] == 60
        assert data["config"]["max_retries"] == 3


@pytest.mark.asyncio
class TestMCPProxyCall:
    """`POST /api/mcp/call` is the real caller of `proxy.call_tool`.

    The JSON-RPC transport is not wired yet, so the route has to surface that
    as an explicit failure.  It used to answer 200 with `{"ok": true, "result":
    "stub ..."}`, which no caller could tell from a real tool result.
    """

    async def test_call_without_transport_returns_501_not_a_stub(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server(
            "mcp-fetch", "1.0.0", "stdio", config={"cmd": ["sleep", "infinity"]}
        )
        await mcp_store.add_attachment("mcp-fetch", "all", None)

        resp = await client.post(
            "/api/mcp/call",
            json={
                "server_id": "mcp-fetch",
                "tool": "fetch_url",
                "agent_name": "weatherbot",
                "arguments": {"url": "https://example.invalid"},
            },
        )
        assert resp.status_code == 501
        data = resp.json()
        assert data["error"] == "not_implemented"
        assert data.get("ok") is not True
        assert "stub" not in str(data.get("result", ""))

    async def test_call_denied_by_permissions_still_returns_403(self, app_client):
        """The new 501 must not shadow the permission check that runs first."""
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.post(
            "/api/mcp/call",
            json={
                "server_id": "mcp-fetch",
                "tool": "fetch_url",
                "agent_name": "weatherbot",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "permission_denied"


@pytest.mark.asyncio
class TestMCPGetEnv:
    async def test_env_empty_when_no_secrets(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers/mcp-fetch/env")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert data["env_keys"] == []

    async def test_env_returns_keys_for_server(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        secrets_store = app.state.secrets
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")
        await secrets_store.add("mcp:mcp-fetch:API_KEY", "secret123", category="general")
        await secrets_store.add("mcp:mcp-fetch:TOKEN", "token456", category="general")

        resp = await client.get("/api/mcp/servers/mcp-fetch/env")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert set(data["env_keys"]) == {"API_KEY", "TOKEN"}


@pytest.mark.asyncio
class TestMCPGetUsedBy:
    async def test_used_by_empty_when_no_attachments(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

        resp = await client.get("/api/mcp/servers/mcp-fetch/used-by")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert data["agents"] == []

    async def test_used_by_returns_agent_attachments(self, app_client):
        client, app = app_client
        mcp_store = app.state.mcp_store
        await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")
        await mcp_store.add_attachment("mcp-fetch", "agent", "weatherbot")
        await mcp_store.add_attachment("mcp-fetch", "all", None)
        await mcp_store.add_attachment("mcp-fetch", "group", "research")

        resp = await client.get("/api/mcp/servers/mcp-fetch/used-by")
        assert resp.status_code == 200
        data = resp.json()
        assert data["server_id"] == "mcp-fetch"
        assert len(data["agents"]) == 2
        scope_kinds = {a["scope_kind"] for a in data["agents"]}
        assert scope_kinds == {"agent", "all"}


@pytest.mark.asyncio
class TestMCPGetThemeSchema:
    async def test_get_theme_schema_returns_vocabulary(self, client):
        resp = await client.get("/api/mcp/tools/get_theme_schema")
        assert resp.status_code == 200
        data = resp.json()
        assert "tokens" in data
        assert "structure" in data
        assert "effects" in data
        assert "safety_floor" in data
        assert "asset_limits" in data
        assert isinstance(data["tokens"], list)
        assert len(data["tokens"]) > 0
        assert isinstance(data["structure"], dict)
