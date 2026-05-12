"""B-tier conformance for ui.notify.

Pass 1 uses the single-user NotificationStore (source field carries the
agent attribution); multi-user routing lands in Pass 2 along with a
NotificationStore migration. Tests here only cover what Pass 1 ships.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_notify_succeeds_with_ui_notify_scope(client, app):
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="conf-notif-1", user_id="u", scope=["ui.notify"])
    resp = await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "T", "body": "B"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is True
    assert body["notification_id"].startswith("ntf_")


@pytest.mark.asyncio
async def test_notify_scope_denied(client, app):
    """A narrow scope without ui.notify must 403."""
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="conf-notif-2", user_id="u", scope=["agents.list"])
    resp = await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "T", "body": "B"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "scope_denied"


@pytest.mark.asyncio
async def test_notify_requires_agent_bearer(client):
    """Session-cookie callers (no bearer) get 401 auth_required."""
    resp = await client.post(
        "/api/ui/notify",
        json={"title": "T", "body": "B"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "auth_required"


@pytest.mark.asyncio
async def test_notify_invalid_priority_is_400(client, app):
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="conf-notif-3", user_id="u", scope=["*"])
    resp = await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "T", "body": "B", "priority": "urgent"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_priority"
    assert "urgent" in body["detail"]


@pytest.mark.asyncio
async def test_notify_lands_in_store_with_agent_source(client, app):
    """The notification is persisted with source='agent:<name>' for audit."""
    store = app.state.agent_tokens_store
    agent_id = f"conf-audit-{uuid.uuid4().hex[:8]}"
    token, _ = await store.issue(agent_id=agent_id, user_id="u-audit", scope=["*"])
    unique_title = f"conf-audit-title-{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": unique_title, "body": "audit body"},
    )
    items = await app.state.notifications.list(limit=50)
    matches = [n for n in items if n["title"] == unique_title]
    assert len(matches) == 1
    assert matches[0]["source"] == f"agent:{agent_id}"
    assert matches[0]["message"] == "audit body"


@pytest.mark.asyncio
async def test_notify_app_origin_prefix_preserved(client, app):
    """An explicit app_origin still ends up prefixed with 'agent:'."""
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="conf-origin-1", user_id="u", scope=["*"])
    unique_title = f"conf-origin-title-{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/ui/notify",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": unique_title,
            "body": "x",
            "app_origin": "my-display-name",
        },
    )
    items = await app.state.notifications.list(limit=50)
    matches = [n for n in items if n["title"] == unique_title]
    assert matches
    # source field is 'agent:<app_origin>' — agent: prefix added when caller
    # supplies a plain origin without the prefix.
    assert matches[0]["source"].startswith("agent:")
    assert "my-display-name" in matches[0]["source"]
