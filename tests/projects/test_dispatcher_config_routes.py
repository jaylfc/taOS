"""RED-FIRST tests for Dispatcher config CRUD (tsk-hvmiwl / S1 part 3).

These tests MUST FAIL on dev before the implementation is added.

Extended by tsk-o4uesr (fix-forward of #3383): real persistence across a store
reopen, agent ownership + liveness on ``eligible_agents``, Bearer handling that
does not shadow a session, strict PUT input, admin ?user_id= for a real user
only, and a cross-user read/write probe that uses an EXISTING target.
"""
from __future__ import annotations

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.projects.dispatcher_store import DispatcherStore


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


def _bearer_client(app, token: str, session_token: str | None = None):
    """Return a client presenting ``Authorization: Bearer <token>``.

    With *session_token* the client is a signed-in browser that also carries a
    (possibly stale) Bearer header, which is the shape that used to be refused.
    """
    cookies = {"taos_session": session_token} if session_token else None
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
        cookies=cookies,
        event_hooks=csrf_event_hooks(),
    )


async def _register_agent(app, display_name: str, user_id: str) -> dict:
    """Register a fresh agent in the registry owned by *user_id*."""
    store = app.state.agent_registry
    await store.init()
    return await store.register(
        framework="test",
        display_name=display_name,
        user_id=user_id,
        origin="taos-deployed",
        handle=display_name.lower().replace(" ", "-"),
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


def _detail_text(r) -> str:
    """Flatten a FastAPI error body (string detail or pydantic error list)."""
    detail = r.json().get("detail", "")
    if isinstance(detail, list):
        detail = " ".join(str(d) for d in detail)
    return str(detail)


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
async def test_put_config_persists_across_store_reopen(app, tmp_data_dir):
    """PUT config, then read it back from a FRESH store on the same projects.db.

    Round-tripping through the live store proves nothing about persistence: an
    in-memory-only store passes it. The assertion therefore comes from a new
    DispatcherStore that never saw the PUT.
    """
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        uid = app.state.auth.find_user("testuser")["id"]
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

        fresh = DispatcherStore(tmp_data_dir / "projects.db")
        await fresh.init()
        try:
            saved = await fresh.get_config(uid)
            # A user who never saved anything reads back as never-updated, so the
            # reopened store can tell a stored row from synthesised defaults.
            never_saved = await fresh.get_config("nobody-ever-configured-this")
        finally:
            await fresh.close()

        assert saved.user_id == uid
        assert saved.enabled is True
        assert saved.boards == [project_id]
        assert saved.eligible_agents == []
        assert saved.max_concurrent_per_agent == 1
        assert saved.poll_seconds == 60
        assert saved.lease_seconds == 1800
        assert saved.updated_by == uid
        assert saved.updated_at is not None and saved.updated_at > 0
        assert never_saved.enabled is False
        assert never_saved.updated_by == ""
        assert never_saved.updated_at is None


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
    """A member's config is private: 403 across users, admin reads it, own id is fine."""
    async with app.router.lifespan_context(app):
        # Create admin user first
        await _ensure_user(app, "admin", "adminpass123", is_admin=True)
        # Make two non-admin users via invite
        for name in ("user1", "user2"):
            invite_code = app.state.auth.add_user_invite(name, "admin")
            app.state.auth.complete_invite(name, invite_code, f"{name} Name", "", f"{name}pass123")
        user1_id = app.state.auth.find_user("user1")["id"]
        user2_id = app.state.auth.find_user("user2")["id"]

        # user2 stores a config worth protecting
        async with _auth_client(app, "user2") as c2:
            r = await c2.put(
                "/api/dispatcher/config",
                json={
                    "enabled": False,
                    "boards": [],
                    "eligible_agents": [],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 45,
                    "lease_seconds": 1200,
                },
            )
            assert r.status_code == 200, r.text

        async with _auth_client(app, "user1") as c1:
            # Naming your OWN id in ?user_id= is still your own config, not a 403
            r = await c1.get(f"/api/dispatcher/config?user_id={user1_id}")
            assert r.status_code == 200, f"Own id in ?user_id=: {r.status_code}: {r.text}"

            # Another EXISTING user's config -> 403 on read
            r = await c1.get(f"/api/dispatcher/config?user_id={user2_id}")
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

            # ... and on write, with user2's stored values left untouched
            r = await c1.put(
                f"/api/dispatcher/config?user_id={user2_id}",
                json={
                    "enabled": True,
                    "boards": [],
                    "eligible_agents": [],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 99,
                    "lease_seconds": 900,
                },
            )
            assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"

        # user2 still sees her own config, unchanged
        async with _auth_client(app, "user2") as c2:
            r = await c2.get("/api/dispatcher/config")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["enabled"] is False
            assert body["poll_seconds"] == 45
            assert body["lease_seconds"] == 1200

        # the admin can read it
        async with _admin_client(app) as ca:
            r = await ca.get(f"/api/dispatcher/config?user_id={user2_id}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["user_id"] == user2_id
            assert body["poll_seconds"] == 45


@pytest.mark.asyncio
async def test_admin_user_id_for_unknown_user_is_404(app):
    """An admin targeting a user that does not exist -> 404, and no orphan row."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "admin", "adminpass123", is_admin=True)
        async with _admin_client(app) as ca:
            r = await ca.get("/api/dispatcher/config?user_id=does-not-exist")
            assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

            r = await ca.put(
                "/api/dispatcher/config?user_id=does-not-exist",
                json={
                    "enabled": True,
                    "boards": [],
                    "eligible_agents": [],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 30,
                    "lease_seconds": 900,
                },
            )
            assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"

        cfg = await app.state.dispatcher_store.get_config("does-not-exist")
        assert cfg.updated_by == "", "a config row was written for a user that does not exist"
        assert cfg.updated_at is None, "a config row was written for a user that does not exist"


@pytest.mark.asyncio
async def test_put_config_accepts_own_active_agent(app):
    """Positive control for the ownership + liveness gate on eligible_agents."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        uid = app.state.auth.find_user("testuser")["id"]
        agent = await _register_agent(app, "My Agent", user_id=uid)
        async with _auth_client(app, "testuser") as c:
            r = await c.put(
                "/api/dispatcher/config",
                json={
                    "enabled": True,
                    "boards": [],
                    "eligible_agents": [agent["canonical_id"]],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 30,
                    "lease_seconds": 900,
                },
            )
            assert r.status_code == 200, r.text
            assert r.json()["eligible_agents"] == [agent["canonical_id"]]


@pytest.mark.asyncio
async def test_put_config_rejects_another_users_agent(app):
    """422 when an eligible agent belongs to somebody else."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        other = await _register_agent(app, "Someone Elses Agent", user_id="user-somebody-else")
        async with _auth_client(app, "testuser") as c:
            r = await c.put(
                "/api/dispatcher/config",
                json={
                    "enabled": True,
                    "boards": [],
                    "eligible_agents": [other["canonical_id"]],
                    "max_concurrent_per_agent": 1,
                    "poll_seconds": 30,
                    "lease_seconds": 900,
                },
            )
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
            detail = _detail_text(r)
            assert "owned" in detail.lower(), detail

            # Nothing was stored for the caller.
            stored = await app.state.dispatcher_store.get_config(
                app.state.auth.find_user("testuser")["id"]
            )
            assert stored.eligible_agents == []


@pytest.mark.asyncio
async def test_put_config_rejects_inactive_agent(app):
    """422 while the agent is not active; accepted again once reactivated."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        uid = app.state.auth.find_user("testuser")["id"]
        agent = await _register_agent(app, "Retired Agent", user_id=uid)
        canonical_id = agent["canonical_id"]
        await app.state.agent_registry.set_status(canonical_id, "suspended")

        payload = {
            "enabled": True,
            "boards": [],
            "eligible_agents": [canonical_id],
            "max_concurrent_per_agent": 1,
            "poll_seconds": 30,
            "lease_seconds": 900,
        }
        async with _auth_client(app, "testuser") as c:
            r = await c.put("/api/dispatcher/config", json=payload)
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
            detail = _detail_text(r)
            assert "active" in detail.lower(), detail

        await app.state.agent_registry.set_status(canonical_id, "active")
        async with _auth_client(app, "testuser") as c:
            r = await c.put("/api/dispatcher/config", json=payload)
            assert r.status_code == 200, r.text
            assert r.json()["eligible_agents"] == [canonical_id]


@pytest.mark.asyncio
async def test_put_config_rejects_unknown_and_mistyped_fields(app):
    """extra="forbid" + strict=True: a typo or a coerced string is a 422."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        base = {
            "enabled": True,
            "boards": [],
            "eligible_agents": [],
            "max_concurrent_per_agent": 1,
            "poll_seconds": 30,
            "lease_seconds": 900,
        }
        async with _auth_client(app, "testuser") as c:
            # Misspelled field name: {"enabeld": true} was a silent 200
            r = await c.put("/api/dispatcher/config", json={**base, "enabeld": True})
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

            # String that pydantic used to coerce: "yes" -> true
            r = await c.put("/api/dispatcher/config", json={**base, "enabled": "yes"})
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

            # String in an int field
            r = await c.put("/api/dispatcher/config", json={**base, "poll_seconds": "30"})
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

            # None is not a list of ids
            r = await c.put("/api/dispatcher/config", json={**base, "boards": None})
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

            # None of the rejected payloads were stored.
            stored = await app.state.dispatcher_store.get_config(
                app.state.auth.find_user("testuser")["id"]
            )
            assert stored.enabled is False
            assert stored.updated_by == ""


@pytest.mark.asyncio
async def test_put_config_caps_and_dedupes_id_lists(app):
    """Repeated ids collapse to one entry; more than 200 distinct ids is a 422."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        uid = app.state.auth.find_user("testuser")["id"]
        first = await _register_agent(app, "Agent Zero", user_id=uid)
        agents = [first]
        for i in range(1, 201):
            agents.append(await _register_agent(app, f"Filler Agent {i}", user_id=uid))

        base = {
            "enabled": True,
            "boards": [],
            "max_concurrent_per_agent": 1,
            "poll_seconds": 30,
            "lease_seconds": 900,
        }
        async with _auth_client(app, "testuser") as c:
            # 5000 copies of one id must not become 5000 registry lookups and a
            # 5000-entry stored list.
            r = await c.put(
                "/api/dispatcher/config",
                json={**base, "eligible_agents": [first["canonical_id"]] * 5000},
            )
            assert r.status_code == 200, r.text
            assert r.json()["eligible_agents"] == [first["canonical_id"]]

            stored = await app.state.dispatcher_store.get_config(uid)
            assert stored.eligible_agents == [first["canonical_id"]]

            # 201 distinct ids is one over the cap.
            r = await c.put(
                "/api/dispatcher/config",
                json={**base, "eligible_agents": [a["canonical_id"] for a in agents]},
            )
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"

            # Same cap on boards (an over-long list is refused before any lookup).
            r = await c.put(
                "/api/dispatcher/config",
                json={**base, "boards": [f"prj-{i}" for i in range(201)]},
            )
            assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_session_cookie_survives_a_stale_bearer_header(app):
    """A signed-in browser carrying a stale Bearer header is served, not refused."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        record = app.state.auth.find_user("testuser")
        session = app.state.auth.create_session(user_id=record["id"], long_lived=True)
        async with _bearer_client(
            app, "stale-or-garbage-token", session_token=session
        ) as c:
            r = await c.get("/api/dispatcher/config")
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
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
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_anonymous_garbage_bearer_is_401_not_403(app):
    """No session and an unrecognised Bearer is unauthenticated (401), not forbidden."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        async with _bearer_client(app, "stale-or-garbage-token") as c:
            r = await c.get("/api/dispatcher/config")
            assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"
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
            assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_admin_local_token_can_read_and_write_config(app):
    """The admin's host local token is a valid credential for config CRUD."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "admin", "adminpass123", is_admin=True)
        local_token = app.state.auth.get_local_token()
        async with _bearer_client(app, local_token) as c:
            r = await c.get("/api/dispatcher/config")
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
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
            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_agent_token_cannot_write_config(app):
    """An agent registry JWT alone is not a credential for config CRUD -> 401.

    /api/dispatcher/config is no longer on _AGENT_TOKEN_PATHS (that allowlist's
    contract is "the route verifies the JWT + scope grant", and this route
    refuses agents), so the middleware stops letting a Bearer past the auth gate
    and the request is unauthenticated rather than forbidden.
    """
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
            assert r.status_code == 401, f"Expected 401, got {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_never_stored_config_reports_updated_at_null(app):
    """A user who has never saved a config has updated_at null, not a fake stamp."""
    async with app.router.lifespan_context(app):
        await _ensure_user(app, "testuser", "testpass123")
        async with _auth_client(app, "testuser") as c:
            r = await c.get("/api/dispatcher/config")
            assert r.status_code == 200, r.text
            assert r.json()["updated_at"] is None
            assert r.json()["updated_by"] == ""


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