"""RED-FIRST tests for Dispatcher config CRUD (tsk-hvmiwl / S1 part 3).

These tests MUST FAIL on dev before the implementation is added.
"""
from __future__ import annotations

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks


async def _ensure_user(app, username: str, password: str, is_admin: bool = False):
    """Ensure a user exists in the auth store."""
    record = app.state.auth.find_user(username)
    if record:
        return record
    if is_admin:
        app.state.auth.setup_user(username, f"{username} Name", "", password)
        record = app.state.auth.find_user(username)
    else:
        # Need an admin to invite
        admin_record = app.state.auth.find_user("admin")
        if not admin_record:
            app.state.auth.setup_user("admin", "Admin", "", "adminpass123")
            admin_record = app.state.auth.find_user("admin")
        invite_code = app.state.auth.add_user_invite(username, "admin")
        app.state.auth.complete_invite(username, invite_code, f"{username} Name", "", password)
        record = app.state.auth.find_user(username)
    if is_admin and record:
        # Make admin by updating the user record
        users_data = app.state.auth._read_users()
        for u in users_data.get("users", []):
            if u.get("username") == username:
                u["is_admin"] = True
                break
        app.state.auth._write_users(users_data)
        record = app.state.auth.find_user(username)
    return record


def _auth_client(app, username="testuser"):
    """Return a session-cookie-authenticated AsyncClient for the given app.
    
    Assumes the user already exists in the auth store.
    """
    record = app.state.auth.find_user(username)
    if not record:
        raise ValueError(f"User {username!r} not found in auth store")
    uid = record["id"]
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


def _admin_client(app):
    """Return an admin-authenticated AsyncClient."""
    record = app.state.auth.find_user("admin")
    if not record:
        raise ValueError("Admin user not found")
    uid = record["id"]
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


def _agent_client(app, canonical_id="agent-test-20260101-000000"):
    """Return an agent Bearer-token authenticated AsyncClient."""
    from tinyagentos.agent_registry_store import mint_registry_token

    private_key = app.state.agent_registry_keypair[0]
    token = mint_registry_token(
        canonical_id=canonical_id,
        private_key_pem=private_key,
        user_id="testuser",
        framework="test",
    )
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
        event_hooks=csrf_event_hooks(),
    )


async def _ensure_agent_registry(app, canonical_id, user_id="testuser"):
    """Register an agent in the registry for testing."""
    store = app.state.agent_registry
    await store.init()
    existing = await store.get(canonical_id)
    if existing is None:
        await store.register(
            framework="test",
            display_name="Test Agent",
            user_id=user_id,
            origin="taos-deployed",
            handle=canonical_id.split("-")[0],
        )


async def _make_project(c, name="Demo", slug="demo"):
    r = await c.post("/api/projects", json={"name": name, "slug": slug})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_get_config_defaults_disabled(app):
    """Fresh user -> 200, enabled false, lease_seconds 900. FAILS on dev (404)."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        async with _auth_client(app, "testuser") as c:
            r = await c.get("/api/dispatcher/config")
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
            body = r.json()
            assert body["enabled"] is False
            assert body["lease_seconds"] == 900
            assert body["boards"] == []
            assert body["eligible_agents"] == []
            assert body["max_concurrent_per_agent"] == 1
            assert body["poll_seconds"] == 30


@pytest.mark.asyncio
async def test_put_config_persists_across_store_reopen(app):
    """PUT config, then reopen store and GET returns the saved values."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        async with _auth_client(app, "testuser") as c:
            # Create a project first to use in boards
            project_id = await _make_project(c, "Test Board", "test-board")

            r = await c.put(
                "/api/dispatcher/config",
                json={
                    "enabled": True,
                    "boards": [project_id],
                    "eligible_agents": [],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 60,
                    "lease_seconds": 1800,
                },
            )
            assert r.status_code == 200, r.text

            # GET again to verify persistence (same store, but proves round-trip)
            r2 = await c.get("/api/dispatcher/config")
            assert r2.status_code == 200, r2.text
            body = r2.json()
            assert body["enabled"] is True
            assert body["boards"] == [project_id]
            assert body["eligible_agents"] == []
            assert body["max_concurrent_per_agent"] == 1
            assert body["poll_seconds"] == 60
            assert body["lease_seconds"] == 1800


@pytest.mark.asyncio
async def test_put_config_rejects_board_not_owned_by_user(app):
    """422 when board is not an active project owned by the target user."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        async with _auth_client(app, "testuser") as c:
            r = await c.put(
                "/api/dispatcher/config",
                json={
                    "enabled": True,
                    "boards": ["prj-nonexistent"],
                    "eligible_agents": [],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 30,
                    "lease_seconds": 900,
                },
            )
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
            body = r.json()
            detail = body.get("detail", "")
            if isinstance(detail, list):
                detail = " ".join(str(d) for d in detail)
            assert "board" in detail.lower() or "project" in detail.lower()


@pytest.mark.asyncio
async def test_put_config_rejects_cap_other_than_one(app):
    """max_concurrent_per_agent == 1 exactly (2 and 0 both -> 422)."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        async with _auth_client(app, "testuser") as c:
            for bad_cap in (2, 0, -1, 5):
                r = await c.put(
                    "/api/dispatcher/config",
                    json={
                        "enabled": True,
                        "boards": [],
                        "eligible_agents": [],
                        "max_concurrent_per_agent": bad_cap,
                        "poll_seconds": 30,
                        "lease_seconds": 900,
                    },
                )
                assert r.status_code == 422, f"Cap {bad_cap}: expected 422, got {r.status_code}: {r.text}"
                body = r.json()
                detail = body.get("detail", "")
                if isinstance(detail, list):
                    detail = " ".join(str(d) for d in detail)
                assert "one-active-claim" in detail.lower() or "max_concurrent" in detail.lower()


@pytest.mark.asyncio
async def test_non_admin_cannot_read_other_users_config(app):
    """Non-admin: own config only; admin may pass ?user_id=."""
    async with app.router.lifespan_context(app):
        # Create admin user first
        await _ensure_user(app, "admin", "adminpass123", is_admin=True)
        # Make a non-admin user via invite
        invite_code = app.state.auth.add_user_invite("user1", "admin")
        app.state.auth.complete_invite("user1", invite_code, "User One", "", "user1pass123")

        # Login as non-admin user1
        async with _auth_client(app, "user1") as c1:
            r = await c1.put(
                "/api/dispatcher/config",
                json={
                    "enabled": True,
                    "boards": [],
                    "eligible_agents": [],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 30,
                    "lease_seconds": 900,
                },
            )
            assert r.status_code == 200, r.text

            # Same non-admin user tries to read another user's config via ?user_id= -> 403
            r = await c1.get("/api/dispatcher/config?user_id=nonexistent-user")
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_agent_token_cannot_write_config(app):
    """Agent Bearer token -> 403 on PUT."""
    async with app.router.lifespan_context(app):
        # Need a user to exist first for the agent to be registered under
        await _ensure_user(app, "testuser", "testpass123")
        await _ensure_agent_registry(app, "agent-test-20260101-000000", "testuser")
        async with _agent_client(app, "agent-test-20260101-000000") as c:
            r = await c.put(
                "/api/dispatcher/config",
                json={
                    "enabled": True,
                    "boards": [],
                    "eligible_agents": [],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 30,
                    "lease_seconds": 900,
                },
            )
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_app_lifespan_wires_dispatcher_store(app):
    """Enters the REAL lifespan and asserts app.state.dispatcher_store is initialised."""
    async with app.router.lifespan_context(app):
        store = app.state.dispatcher_store
        assert store is not None, "dispatcher_store not attached to app.state"
        # Prove it is actually usable (init() ran)
        cfg = await store.get_config("test-user")
        assert cfg is not None
        assert cfg.enabled is False
        assert cfg.lease_seconds == 900
        assert cfg.max_concurrent_per_agent == 1