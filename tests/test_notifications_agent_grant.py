"""Agent-token path for POST /api/notifications (notifications_write grant gating)."""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_routes_decisions_agent.py)
# ---------------------------------------------------------------------------

async def _mint_agent(app, project_id, scopes, handle="@taOS-dev"):
    """Register an active agent, grant it *scopes* for *project_id*, return
    (canonical_id, bearer_token). project_id=None grants globally."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store in (registry, grants):
        if store._db is None:
            await store.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code",
        display_name="taOS dev",
        allow_reserved=True,
        origin="internal",
        handle=handle,
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="claude-code", project_id=project_id
    )
    return cid, token


def _agent_client(app, token):
    """Cookieless client that authenticates only via the agent bearer token."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _new_project(client, name="alpha", slug="alpha"):
    resp = await client.post("/api/projects", json={"name": name, "slug": slug})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_with_global_grant_posts_and_admin_receives(client):
    """(a) A global (null-project) notifications_write agent posts 200 and the
    notification is addressed to the instance admin."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    cid, token = await _mint_agent(app, None, ("notifications_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "spec ready",
            "message": "ready for review",
            "level": "info",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == {"ok": True}
    # Verify the store row: source is the agent canonical_id.
    store = app.state.notifications
    items = await store.list()
    assert len(items) == 1
    assert items[0]["source"] == f"agent:{cid}"
    assert items[0]["user_id"] == admin_id
    assert items[0]["title"] == "spec ready"
    assert items[0]["data"] == {"from_agent": cid}


@pytest.mark.asyncio
async def test_agent_with_project_grant_notifies_project_owner_only(client):
    """(b) A per-project notifications_write grant posts to that project's
    owner only, not to other users."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("notifications_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "project update",
            "message": "done",
            "level": "info",
            "data": {"project_id": pid},
        })
    assert resp.status_code == 200, resp.text
    store = app.state.notifications
    items = await store.list(user_id=admin_id)
    assert len(items) == 1
    assert items[0]["source"] == f"agent:{cid}"
    assert items[0]["user_id"] == admin_id


@pytest.mark.asyncio
async def test_agent_without_grant_refused(client):
    """(c) An agent without a notifications_write grant gets 403."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("a2a_send",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "no auth",
            "message": "nope",
        })
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_agent_cannot_spoof_source(client):
    """(d) The body source is ignored on the agent path; the store row always
    shows source = agent:<canonical_id>."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("notifications_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "spoof attempt",
            "message": "trying to look like system",
            "level": "info",
            "source": "system",
        })
    assert resp.status_code == 200, resp.text
    store = app.state.notifications
    items = await store.list()
    assert len(items) == 1
    assert items[0]["source"] == f"agent:{cid}"


@pytest.mark.asyncio
async def test_agent_error_level_refused(client):
    """(e) error level is refused for agent posts."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("notifications_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "bad level",
            "message": "nope",
            "level": "error",
        })
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_agent_caps(client):
    """(e) Title > 120, message > 1000, and data > 4 KB are all refused with 422."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("notifications_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "x" * 121,
            "message": "ok",
        })
    assert resp.status_code == 422, resp.text

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "long msg",
            "message": "m" * 1001,
        })
    assert resp.status_code == 422, resp.text

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/notifications", json={
            "title": "big data",
            "message": "ok",
            "data": {"k": "v" * 5000},
        })
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_agent_rate_limited(client):
    """(e) After 10 posts in 10 minutes the agent gets 429 with retry_after."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("notifications_write",))
    # Reset the limiter window so we start clean.
    from tinyagentos.routes.notifications import _agent_notif_rate_hits
    _agent_notif_rate_hits.clear()

    async with _agent_client(app, token) as ac:
        for _ in range(10):
            resp = await ac.post("/api/notifications", json={
                "title": "rate",
                "message": "test",
            })
            assert resp.status_code == 200, resp.text
        # 11th should be rate-limited.
        resp = await ac.post("/api/notifications", json={
            "title": "over",
            "message": "limit",
        })
    assert resp.status_code == 429, resp.text
    body = resp.json()
    assert body["error"] == "rate_limited"
    assert "retry_after" in body


@pytest.mark.asyncio
async def test_notifications_write_in_every_scope_list():
    """(f) notifications_write is present in every scope list the system uses."""
    from tinyagentos.routes.agent_auth_requests import VALID_SCOPES
    from tinyagentos.routes.agent_registry import _ALLOWED_SCOPES
    from tinyagentos.native_agent_identity import SYSTEM_AGENT_API_SCOPES
    from tinyagentos.routes.project_invites import _PROJECT_SCOPED

    assert "notifications_write" in VALID_SCOPES, "missing from VALID_SCOPES"
    assert "notifications_write" in _ALLOWED_SCOPES, "missing from _ALLOWED_SCOPES"
    assert "notifications_write" in SYSTEM_AGENT_API_SCOPES, "missing from SYSTEM_AGENT_API_SCOPES"
    assert "notifications_write" in _PROJECT_SCOPED, "missing from _PROJECT_SCOPED"


@pytest.mark.asyncio
async def test_admin_session_path_accepts_long_title_and_message(client):
    """RED test (h): through the app, as an admin session, POST a 200-char title and a 3000-char message -> 200 and the row is stored with those exact lengths."""
    # Clear any existing notifications
    store = client._transport.app.state.notifications
    await store.add("pre-existing", "x", source="system")
    
    # Test with 200-char title and 3000-char message
    long_title = "x" * 200
    long_message = "y" * 3000
    resp = await client.post("/api/notifications", json={
        "title": long_title,
        "message": long_message,
        "level": "error",
        "source": "system",
    })
    
    # Should succeed (200)
    assert resp.status_code == 200, resp.text
    
    # Verify the row is stored with those exact lengths
    items = await store.list()
    assert len(items) >= 2  # At least the new one and the pre-existing one
    
    # Find the posted notification by its unique title (not by list position;
    # list() orders by timestamp DESC so items[-1] is the OLDEST row).
    posted = next((i for i in items if i["title"] == long_title), None)
    assert posted is not None, "posted notification not found"
    assert len(posted["title"]) == 200
    assert len(posted["message"]) == 3000
    assert posted["title"] == long_title
    assert posted["message"] == long_message
