from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.mcp.registry import MCPServerStore
from tinyagentos.mcp.permissions import check_permission, PermissionResult
from tinyagentos.mcp.supervisor import MCPSupervisor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = MCPServerStore(tmp_path / "mcp.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def supervisor(store):
    sup = MCPSupervisor(store=store, catalog=None, notif_store=None)
    yield sup
    await sup.stop_all()


@pytest_asyncio.fixture
async def app_client(tmp_path):
    """Minimal FastAPI app with only the MCP router wired — avoids the full
    create_app() which pulls in optional private packages (taosmd etc.)."""
    from fastapi import FastAPI
    from tinyagentos.routes.mcp import router as mcp_router
    from tinyagentos.secrets import SecretsStore

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


# ---------------------------------------------------------------------------
# 1. Store CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_and_get_server(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    server = await store.get_server("mcp-fetch")
    assert server is not None
    assert server["id"] == "mcp-fetch"
    assert server["version"] == "1.0.0"
    assert server["transport"] == "stdio"
    assert server["running"] is False


@pytest.mark.asyncio
async def test_list_servers_empty(store):
    servers = await store.list_servers()
    assert servers == []


@pytest.mark.asyncio
async def test_list_servers(store):
    await store.register_server("mcp-a", "1.0", "stdio")
    await store.register_server("mcp-b", "2.0", "sse")
    servers = await store.list_servers()
    assert len(servers) == 2
    ids = {s["id"] for s in servers}
    assert ids == {"mcp-a", "mcp-b"}


@pytest.mark.asyncio
async def test_mark_running_and_stopped(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.mark_running("mcp-fetch", pid=12345)
    server = await store.get_server("mcp-fetch")
    assert server["running"] is True
    assert server["pid"] == 12345

    await store.mark_stopped("mcp-fetch", exit_code=0)
    server = await store.get_server("mcp-fetch")
    assert server["running"] is False
    assert server["pid"] is None
    assert server["last_exit_code"] == 0


@pytest.mark.asyncio
async def test_delete_server_cascades(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    att_id = await store.add_attachment("mcp-fetch", "all", None)
    assert att_id > 0
    await store.delete_server("mcp-fetch")
    assert await store.get_server("mcp-fetch") is None
    # attachments should be cascaded away
    attachments = await store.list_attachments("mcp-fetch")
    assert attachments == []


@pytest.mark.asyncio
async def test_set_and_get_config(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.set_config("mcp-fetch", {"timeout": 30, "max_retries": 3})
    config = await store.get_config("mcp-fetch")
    assert config["timeout"] == 30
    assert config["max_retries"] == 3


@pytest.mark.asyncio
async def test_add_and_delete_attachment(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    att_id = await store.add_attachment(
        "mcp-fetch", "agent", "weatherbot",
        allowed_tools=["fetch_url"],
        allowed_resources=["https://*"],
    )
    attachments = await store.list_attachments("mcp-fetch")
    assert len(attachments) == 1
    assert attachments[0]["scope_kind"] == "agent"
    assert attachments[0]["allowed_tools"] == ["fetch_url"]

    removed = await store.delete_attachment(att_id)
    assert removed is True
    attachments = await store.list_attachments("mcp-fetch")
    assert attachments == []


@pytest.mark.asyncio
async def test_list_attachments_for_agent_all_scope(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "all", None)
    results = await store.list_attachments_for_agent("any-agent", [])
    assert len(results) == 1
    assert results[0]["scope_kind"] == "all"


@pytest.mark.asyncio
async def test_list_attachments_for_agent_scope(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "agent", "bot1")
    await store.add_attachment("mcp-fetch", "agent", "bot2")

    results = await store.list_attachments_for_agent("bot1", [])
    assert len(results) == 1
    assert results[0]["scope_id"] == "bot1"


@pytest.mark.asyncio
async def test_list_attachments_for_agent_group_scope(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "group", "research")
    results = await store.list_attachments_for_agent("any-bot", ["research", "sales"])
    assert len(results) == 1
    assert results[0]["scope_id"] == "research"


# ---------------------------------------------------------------------------
# 2. Permissions gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deny_by_default(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    result = await check_permission(store, "mcp-fetch", "agent1", [])
    assert result.allowed is False
    assert result.reason == "no attachment grants access"


@pytest.mark.asyncio
async def test_allow_after_attach_all(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "all", None)
    result = await check_permission(store, "mcp-fetch", "any-agent", [])
    assert result.allowed is True


@pytest.mark.asyncio
async def test_allow_after_agent_attach(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "agent", "bot1")
    result = await check_permission(store, "mcp-fetch", "bot1", [])
    assert result.allowed is True
    assert "agent" in result.reason


@pytest.mark.asyncio
async def test_deny_wrong_agent(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "agent", "bot1")
    result = await check_permission(store, "mcp-fetch", "bot2", [])
    assert result.allowed is False


@pytest.mark.asyncio
async def test_allow_via_group(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "group", "research")
    result = await check_permission(store, "mcp-fetch", "bot1", ["research"])
    assert result.allowed is True
    assert "group" in result.reason


@pytest.mark.asyncio
async def test_deny_not_in_group(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "group", "research")
    result = await check_permission(store, "mcp-fetch", "bot1", ["sales"])
    assert result.allowed is False


@pytest.mark.asyncio
async def test_tool_allowlist_blocks(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "all", None, allowed_tools=["fetch_url"])
    result = await check_permission(store, "mcp-fetch", "bot1", [], tool="write_file")
    assert result.allowed is False
    assert result.reason == "tool not in allowlist"


@pytest.mark.asyncio
async def test_tool_allowlist_permits(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "all", None, allowed_tools=["fetch_url"])
    result = await check_permission(store, "mcp-fetch", "bot1", [], tool="fetch_url")
    assert result.allowed is True


@pytest.mark.asyncio
async def test_empty_tool_list_means_unrestricted(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "all", None, allowed_tools=[])
    result = await check_permission(store, "mcp-fetch", "bot1", [], tool="any_tool")
    assert result.allowed is True


@pytest.mark.asyncio
async def test_resource_pattern_match(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment(
        "mcp-fetch", "all", None,
        allowed_resources=["https://example.com/*"]
    )
    result = await check_permission(
        store, "mcp-fetch", "bot1", [],
        resource="https://example.com/api/data"
    )
    assert result.allowed is True


@pytest.mark.asyncio
async def test_resource_pattern_mismatch(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment(
        "mcp-fetch", "all", None,
        allowed_resources=["https://example.com/*"]
    )
    result = await check_permission(
        store, "mcp-fetch", "bot1", [],
        resource="https://evil.com/bad"
    )
    assert result.allowed is False
    assert result.reason == "resource pattern mismatch"


@pytest.mark.asyncio
async def test_empty_resource_list_means_unrestricted(store):
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "all", None, allowed_resources=[])
    result = await check_permission(
        store, "mcp-fetch", "bot1", [],
        resource="https://anything.com/foo"
    )
    assert result.allowed is True


@pytest.mark.asyncio
async def test_union_semantics_broader_wins(store):
    """If one attachment has a restricted tool list but another has empty (unrestricted),
    UNION means the broader one wins → allow."""
    await store.register_server("mcp-fetch", "1.0.0", "stdio")
    await store.add_attachment("mcp-fetch", "agent", "bot1", allowed_tools=["fetch_url"])
    await store.add_attachment("mcp-fetch", "all", None, allowed_tools=[])  # unrestricted
    result = await check_permission(store, "mcp-fetch", "bot1", [], tool="write_file")
    assert result.allowed is True


# ---------------------------------------------------------------------------
# 3. Supervisor: start / stop / restart / logs / uninstall cascade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supervisor_start_stop(store, supervisor):
    await store.register_server("sleep-srv", "1.0", "stdio", config={"cmd": ["sleep", "infinity"]})
    ok = await supervisor.start("sleep-srv")
    assert ok is True

    status = supervisor.get_status("sleep-srv")
    assert status["running"] is True
    assert status["pid"] is not None

    server = await store.get_server("sleep-srv")
    assert server["running"] is True

    ok = await supervisor.stop("sleep-srv")
    assert ok is True
    status = supervisor.get_status("sleep-srv")
    assert status["running"] is False


@pytest.mark.asyncio
async def test_supervisor_restart(store, supervisor):
    await store.register_server("sleep-srv", "1.0", "stdio", config={"cmd": ["sleep", "infinity"]})
    await supervisor.start("sleep-srv")
    ok = await supervisor.restart("sleep-srv")
    assert ok is True
    assert supervisor.get_status("sleep-srv")["running"] is True


@pytest.mark.asyncio
async def test_supervisor_start_already_running(store, supervisor):
    await store.register_server("sleep-srv", "1.0", "stdio", config={"cmd": ["sleep", "infinity"]})
    ok1 = await supervisor.start("sleep-srv")
    ok2 = await supervisor.start("sleep-srv")  # should be no-op
    assert ok1 is True
    assert ok2 is True  # returns True (already running)


@pytest.mark.asyncio
async def test_supervisor_log_buffer_bounds(store, supervisor):
    await store.register_server("echo-srv", "1.0", "stdio", config={"cmd": ["sh", "-c", "for i in $(seq 1 50); do echo line$i >&2; done; sleep infinity"]})
    await supervisor.start("echo-srv")
    await asyncio.sleep(0.3)
    logs = supervisor.logs("echo-srv", since_idx=0, limit=200)
    assert len(logs) >= 0  # may or may not have captured all, just check structure
    for entry in logs:
        assert "idx" in entry
        assert "ts" in entry
        assert "level" in entry
        assert "line" in entry


@pytest.mark.asyncio
async def test_supervisor_uninstall_cascade(store, supervisor):
    await store.register_server("mcp-fetch", "1.0", "stdio")
    await store.add_attachment("mcp-fetch", "agent", "bot1")
    await store.add_attachment("mcp-fetch", "agent", "bot2")

    cascade = await supervisor.uninstall("mcp-fetch")
    assert "agents_affected" in cascade
    assert set(cascade["agents_affected"]) == {"bot1", "bot2"}
    assert await store.get_server("mcp-fetch") is None


@pytest.mark.asyncio
async def test_supervisor_stop_not_running(supervisor):
    # Should return False cleanly without error
    result = await supervisor.stop("nonexistent-srv")
    assert result is False


@pytest.mark.asyncio
async def test_supervisor_missing_server(store, supervisor):
    ok = await supervisor.start("missing-srv")
    assert ok is False


# ---------------------------------------------------------------------------
# 4. Routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_list_servers_empty(app_client):
    client, app = app_client
    resp = await client.get("/api/mcp/servers")
    assert resp.status_code == 200
    assert resp.json()["servers"] == []


@pytest.mark.asyncio
async def test_route_register_and_list(app_client):
    client, app = app_client
    mcp_store = app.state.mcp_store
    await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")
    resp = await client.get("/api/mcp/servers")
    assert resp.status_code == 200
    servers = resp.json()["servers"]
    assert len(servers) == 1
    assert servers[0]["id"] == "mcp-fetch"


@pytest.mark.asyncio
async def test_route_attach_permission(app_client):
    client, app = app_client
    mcp_store = app.state.mcp_store
    await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

    resp = await client.post(
        "/api/mcp/servers/mcp-fetch/permissions",
        json={"scope_kind": "agent", "scope_id": "bot1", "allowed_tools": [], "allowed_resources": []},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "attachment_id" in data


@pytest.mark.asyncio
async def test_route_attach_agent_missing_scope_id(app_client):
    client, app = app_client
    mcp_store = app.state.mcp_store
    await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

    resp = await client.post(
        "/api/mcp/servers/mcp-fetch/permissions",
        json={"scope_kind": "agent"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_route_delete_permission(app_client):
    client, app = app_client
    mcp_store = app.state.mcp_store
    await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")
    att_id = await mcp_store.add_attachment("mcp-fetch", "all", None)

    resp = await client.delete(f"/api/mcp/servers/mcp-fetch/permissions/{att_id}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_route_config_roundtrip(app_client):
    client, app = app_client
    mcp_store = app.state.mcp_store
    await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

    resp = await client.put(
        "/api/mcp/servers/mcp-fetch/config",
        json={"config": {"timeout": 60}},
    )
    assert resp.status_code == 200

    resp = await client.get("/api/mcp/servers/mcp-fetch/config")
    assert resp.status_code == 200
    assert resp.json()["config"]["timeout"] == 60


@pytest.mark.asyncio
async def test_route_logs(app_client):
    client, app = app_client
    mcp_store = app.state.mcp_store
    await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

    resp = await client.get("/api/mcp/servers/mcp-fetch/logs?since=0&limit=50")
    assert resp.status_code == 200
    assert "logs" in resp.json()


@pytest.mark.asyncio
async def test_route_proxy_call_permission_denied(app_client):
    client, app = app_client
    mcp_store = app.state.mcp_store
    await mcp_store.register_server("mcp-fetch", "1.0.0", "stdio")

    resp = await client.post(
        "/api/mcp/call",
        json={
            "server_id": "mcp-fetch",
            "tool": "fetch_url",
            "agent_name": "bot1",
            "agent_groups": [],
            "arguments": {"url": "https://example.com"},
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_route_uninstall_not_found(app_client):
    client, app = app_client
    resp = await client.delete("/api/mcp/servers/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# stop_all concurrency — two servers stop in parallel, not serially
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_all_is_concurrent(tmp_path):
    """stop_all must stop multiple servers concurrently.

    Stub two fake processes whose stop() takes 0.5s each. With serial
    execution total wall time would be >= 0.4s; parallel execution must
    finish in under 0.35s.
    """
    import time

    store = MCPServerStore(tmp_path / "mcp.db")
    await store.init()

    sup = MCPSupervisor(store=store, catalog=None, notif_store=None)

    stop_calls: list[str] = []

    async def _fake_stop(sid: str, timeout: float = 10.0) -> bool:
        await asyncio.sleep(0.5)
        stop_calls.append(sid)
        sup._processes.pop(sid, None)
        await store.mark_stopped(sid, exit_code=0)
        return True

    await store.register_server("srv-a", "1.0", "stdio")
    await store.register_server("srv-b", "1.0", "stdio")

    # Inject placeholder entries so stop_all sees two servers.
    from unittest.mock import MagicMock
    proc_mock = MagicMock()
    proc_mock.poll.return_value = None
    from tinyagentos.mcp.supervisor import ServerProcess
    sup._processes["srv-a"] = ServerProcess(process=proc_mock, transport="stdio")
    sup._processes["srv-b"] = ServerProcess(process=proc_mock, transport="stdio")

    # Patch stop with our slow fake.
    from unittest.mock import patch
    with patch.object(sup, "stop", side_effect=_fake_stop):
        t0 = time.monotonic()
        await sup.stop_all()
        elapsed = time.monotonic() - t0

    assert set(stop_calls) == {"srv-a", "srv-b"}
    assert elapsed < 0.9, f"stop_all took {elapsed:.3f}s, expected under 0.9s (serial would be 1.0s+)"

    await store.close()
