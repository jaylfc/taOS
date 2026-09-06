"""Tests for the agent rename (alias) endpoint.

Covers:
  - PATCH display_name updates the stored value and GET reflects it.
  - PATCH with the same display_name is a no-op (no notification emitted).
  - PATCH on an unknown canonical_id returns 404.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks


@pytest_asyncio.fixture
async def client(app, tmp_data_dir):
    """Async client with agent_registry store initialised, authenticated as admin."""
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    notif_store = app.state.notifications
    if notif_store._db is not None:
        await notif_store.close()
    await notif_store.init()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c

    await registry_store.close()
    await notif_store.close()


@pytest.mark.asyncio
async def test_rename_updates_display_name(client):
    """Renaming an agent updates display_name and GET reflects the change."""
    reg = await client.post(
        "/api/agents/registry/register",
        json={"framework": "openclaw", "display_name": "Original Name"},
    )
    assert reg.status_code == 200, reg.text
    canonical_id = reg.json()["canonical_id"]

    patch = await client.patch(
        f"/api/agents/registry/{canonical_id}",
        json={"display_name": "Renamed Agent"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["display_name"] == "Renamed Agent"

    get_resp = await client.get(f"/api/agents/registry/{canonical_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["display_name"] == "Renamed Agent"

    notifs = await client.get("/api/notifications")
    assert notifs.status_code == 200, notifs.text
    items = notifs.json() if isinstance(notifs.json(), list) else notifs.json().get("notifications", [])
    assert any(
        n.get("source") == "agent_registry" and "Renamed Agent" in n.get("message", "")
        for n in items
    ), f"expected agent_registry rename notification, got: {items}"


@pytest.mark.asyncio
async def test_rename_same_value_is_noop(client):
    """PATCH with the same display_name does not emit a notification."""
    reg = await client.post(
        "/api/agents/registry/register",
        json={"framework": "openclaw", "display_name": "Same Name"},
    )
    assert reg.status_code == 200, reg.text
    canonical_id = reg.json()["canonical_id"]

    await client.post("/api/notifications/read-all")

    patch = await client.patch(
        f"/api/agents/registry/{canonical_id}",
        json={"display_name": "Same Name"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["display_name"] == "Same Name"

    notifs = await client.get("/api/notifications")
    assert notifs.status_code == 200, notifs.text
    items = notifs.json() if isinstance(notifs.json(), list) else notifs.json().get("notifications", [])
    agent_notifs = [n for n in items if n.get("source") == "agent_registry"]
    assert len(agent_notifs) == 0, f"expected no agent_registry notifications, got: {agent_notifs}"


@pytest.mark.asyncio
async def test_rename_unknown_agent_returns_404(client):
    """PATCH on a non-existent canonical_id returns 404."""
    patch = await client.patch(
        "/api/agents/registry/nonexistent-20260101-000000",
        json={"display_name": "New Name"},
    )
    assert patch.status_code == 404, patch.text
