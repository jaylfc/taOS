import pytest


@pytest.mark.asyncio
async def test_post_ui_notify_writes_to_store(client, app):
    """A bearer token with ui.notify scope can post a notification; it lands in the store."""
    store = app.state.agent_tokens_store
    plaintext, _ = await store.issue(agent_id="notif-agent", user_id="u1", scope=["ui.notify"])
    resp = await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"title": "Build complete", "body": "PR #449 merged."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is True
    assert body["notification_id"].startswith("ntf_")
    # Landed in the notif store with source identifying the calling agent
    items = await app.state.notifications.list()
    assert any(
        n["title"] == "Build complete" and n["source"] == "agent:notif-agent"
        for n in items
    )


@pytest.mark.asyncio
async def test_post_ui_notify_requires_agent_bearer(client, app):
    """Without a bearer token (session cookie only) the endpoint returns 401."""
    resp = await client.post(
        "/api/ui/notify",
        json={"title": "x", "body": "y"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "auth_required"
    assert body["fix"]
    assert body["doc_url"]


@pytest.mark.asyncio
async def test_post_ui_notify_requires_scope(client, app):
    """A bearer with narrow scope (no ui.notify) returns 403 scope_denied."""
    store = app.state.agent_tokens_store
    plaintext, _ = await store.issue(agent_id="narrow", user_id="u", scope=["agents.list"])
    resp = await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"title": "x", "body": "y"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "scope_denied"


@pytest.mark.asyncio
async def test_post_ui_notify_invalid_priority(client, app):
    """priority outside {low,normal,high} returns canonical 400."""
    store = app.state.agent_tokens_store
    plaintext, _ = await store.issue(agent_id="prio-agent", user_id="u", scope=["ui.notify"])
    resp = await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"title": "x", "body": "y", "priority": "urgent"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_priority"
    assert "urgent" in body["detail"]


@pytest.mark.asyncio
async def test_post_ui_notify_priority_default_normal(client, app):
    """Omitting priority defaults to 'normal' and the store's level reflects that."""
    store = app.state.agent_tokens_store
    plaintext, _ = await store.issue(agent_id="def-agent", user_id="u", scope=["ui.notify"])
    resp = await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"title": "Default", "body": "no priority"},
    )
    assert resp.status_code == 200
