import json
import time

import pytest

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.projects.invite_store import (
    InviteAlreadyRedeemedError,
    InvitePendingCapError,
    InviteRevokedError,
    ProjectInviteStore,
)


async def _create_project(client, slug="invite-proj") -> str:
    resp = await client.post("/api/projects", json={"name": "Invite Proj", "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_mint_returns_invite_id_and_pin(client, app):
    pid = await _create_project(client)
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": ["a2a_send"], "approval_mode": "auto", "check_interval_secs": 1800},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["invite_id"]) == 6
    assert data["invite_id"].isdigit()
    assert len(data["pin"]) == 4
    assert data["pin"].isdigit()
    assert "project_tasks" in data["scopes"]
    assert data["approval_mode"] == "auto"
    assert data["check_interval_secs"] == 1800


@pytest.mark.asyncio
async def test_mint_project_tasks_always_present(client, app):
    pid = await _create_project(client)
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": ["a2a_send"], "approval_mode": "auto"},
    )
    assert resp.status_code == 200
    assert "project_tasks" in resp.json()["scopes"]


@pytest.mark.asyncio
async def test_mint_11th_pending_returns_429(client, app):
    pid = await _create_project(client, slug="invite-cap-proj")
    store = app.state.project_invites
    for i in range(10):
        await store.mint(
            project_id=pid,
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="admin",
        )
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_list_invites_excludes_pin_hash(client, app):
    pid = await _create_project(client)
    await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    resp = await client.get(f"/api/projects/{pid}/invites")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert "pin_hash" not in items[0]


@pytest.mark.asyncio
async def test_revoke_returns_204(client, app):
    pid = await _create_project(client)
    mint_resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    iid = mint_resp.json()["invite_id"]
    resp = await client.delete(f"/api/projects/{pid}/invites/{iid}")
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_non_admin_cannot_mint(client, app):
    pid = await _create_project(client)
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        user_id="non-admin-user", is_admin=False
    )
    try:
        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": [], "approval_mode": "auto"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_revoke_nonexistent_returns_404(client, app):
    pid = await _create_project(client)
    resp = await client.delete(f"/api/projects/{pid}/invites/000000")
    assert resp.status_code == 404, resp.text
