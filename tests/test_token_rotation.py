"""Integration tests for per-identity token rotation (token_min_iat).

Covers the full auth chain: store → auth path → route.
"""

import time

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.agent_token_auth import check_agent_scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def agent_app(app, client):
    """AsyncClient logged in as admin + agent_registry / agent_grants initialised.

    Reuses the shared ``client`` fixture (which initialises every store and
    sets up the admin session) and only adds the agent_registry and
    agent_grants stores on top.  This prevents breakage when new stores get
    added to conftest — there is no copy-paste to drift.
    """
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr, None)
        if store is not None and store._db is None:
            await store.init()
    yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal stand-in for a starlette Request used by check_agent_scope."""

    def __init__(self, app, token: str | None = None):
        self.app = app
        self.headers = {}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"


async def _register_and_mint(app, *, user_id="u", owner_user_id=None, scopes=("a2a_receive",)):
    """Register an active agent, add grants, and mint a signed JWT.

    If *owner_user_id* is given it is passed to ``register(user_id=...)``
    so the DB row's owner matches.  Otherwise the DB row uses the default
    (empty string, admin-only) while the JWT claim gets the separate
    *user_id* value (legacy behaviour for auth-path tests).

    Returns (canonical_id, token).
    """
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="test",
        display_name="TestAgent",
        origin="external-selfjoin",
        handle="@test",
        user_id=owner_user_id if owner_user_id is not None else "",
    )
    cid = rec["canonical_id"]
    await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope)
    token = mint_registry_token(cid, priv, user_id=user_id, framework="test")
    return cid, token


def _make_nonadmin_client(
    app, auth, *, username: str, full_name: str, password: str
) -> tuple[AsyncClient, str]:
    """Create an AsyncClient authenticated as a new non-admin user.

    Uses the invite flow (``add_user_invite`` + ``complete_invite``) because
    ``setup_user`` only works for the first user.
    Returns (AsyncClient, user_id).
    """
    code = auth.add_user_invite(username, "admin")
    auth.complete_invite(username, code, full_name, "", password)
    record = auth.find_user(username)
    uid = record["id"]
    token = auth.create_session(user_id=uid)
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ), uid


# ---------------------------------------------------------------------------
# Auth-path tests
# ---------------------------------------------------------------------------


class TestTokenMinIatAuth:
    """Verify the token_min_iat check inside _verify_agent_scope."""

    @pytest.mark.asyncio
    async def test_old_token_rejected_after_bump(self, app):
        """A token minted before the bump is rejected after bump_token_min_iat."""
        for attr in ("agent_registry", "agent_grants"):
            store = getattr(app.state, attr, None)
            if store is not None and store._db is None:
                await store.init()

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair

        rec = await registry.register(
            framework="test", display_name="TestAgent",
            origin="external-selfjoin", handle="@test",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        await grants.add_grant(cid, "a2a_receive")

        # Mint old token
        old_token = mint_registry_token(cid, priv, user_id="u", framework="test")
        assert (await registry.get(cid)) is not None  # token_min_iat is 0

        # Bump the cutoff to a future timestamp so the old token's iat is
        # strictly less (both happen in sub-second time in tests).
        await registry.bump_token_min_iat(cid, int(time.time()) + 3600)

        # The old token should now be rejected
        req = _FakeRequest(app, old_token)
        with pytest.raises(HTTPException) as exc:
            await check_agent_scope(req, "a2a_receive")
        assert exc.value.status_code == 401
        assert exc.value.detail == "token superseded"

    @pytest.mark.asyncio
    async def test_new_token_passes_after_bump(self, app):
        """A token minted AFTER the bump passes the cutoff check."""
        for attr in ("agent_registry", "agent_grants"):
            store = getattr(app.state, attr, None)
            if store is not None and store._db is None:
                await store.init()

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair

        rec = await registry.register(
            framework="test", display_name="TestAgent",
            origin="external-selfjoin", handle="@test",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        await grants.add_grant(cid, "a2a_receive")

        # Bump the cutoff
        await registry.bump_token_min_iat(cid, int(time.time()))

        # Mint a new token after the bump
        new_token = mint_registry_token(cid, priv, user_id="u", framework="test")

        req = _FakeRequest(app, new_token)
        result = await check_agent_scope(req, "a2a_receive")
        assert result == cid

    @pytest.mark.asyncio
    async def test_default_zero_keeps_existing_tokens_valid(self, app):
        """Default token_min_iat=0 means all tokens pass (no lockout on migration)."""
        for attr in ("agent_registry", "agent_grants"):
            store = getattr(app.state, attr, None)
            if store is not None and store._db is None:
                await store.init()

        registry = app.state.agent_registry
        grants = app.state.agent_grants
        priv, _pub = app.state.agent_registry_keypair

        rec = await registry.register(
            framework="test", display_name="TestAgent",
            origin="external-selfjoin", handle="@test",
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        await grants.add_grant(cid, "a2a_receive")

        # token_min_iat should be 0 by default
        reread = await registry.get(cid)
        assert reread["token_min_iat"] == 0

        token = mint_registry_token(cid, priv, user_id="u", framework="test")
        req = _FakeRequest(app, token)
        result = await check_agent_scope(req, "a2a_receive")
        assert result == cid


# ---------------------------------------------------------------------------
# Route-level tests
# ---------------------------------------------------------------------------


class TestRotateTokensRoute:
    """Test POST /api/agents/registry/{id}/rotate-tokens."""

    @pytest.mark.asyncio
    async def test_admin_can_rotate(self, agent_app, app):
        """An admin can bump token_min_iat on any identity."""
        cid, _token = await _register_and_mint(app, user_id="admin")
        resp = await agent_app.post(f"/api/agents/registry/{cid}/rotate-tokens")
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_min_iat"] > 0

    @pytest.mark.asyncio
    async def test_nonadmin_owner_can_rotate_own_identity(self, agent_app, app):
        """A non-admin session owner can rotate their OWN identity (200)."""
        # Create a non-admin user and register an agent they own.
        client, uid = _make_nonadmin_client(
            app, app.state.auth, username="owner1", full_name="Owner One",
            password="password123",
        )
        async with client:
            cid, _token = await _register_and_mint(
                app, user_id=uid, owner_user_id=uid,
            )
            resp = await client.post(
                f"/api/agents/registry/{cid}/rotate-tokens"
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["token_min_iat"] > 0

    @pytest.mark.asyncio
    async def test_nonadmin_cannot_rotate_others_agent(self, agent_app, app):
        """A non-admin, non-owner rotating someone ELSE'S agent → 403."""
        # First user owns an agent.
        owner_client, owner_uid = _make_nonadmin_client(
            app, app.state.auth, username="owner2", full_name="Owner Two",
            password="password123",
        )
        # Second user tries to rotate the first user's agent.
        intruder_client, intruder_uid = _make_nonadmin_client(
            app, app.state.auth, username="intruder", full_name="Intruder",
            password="password123",
        )
        async with owner_client:
            cid, _token = await _register_and_mint(
                app, user_id=owner_uid, owner_user_id=owner_uid,
            )
        async with intruder_client:
            resp = await intruder_client.post(
                f"/api/agents/registry/{cid}/rotate-tokens"
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_userid_agent_is_admin_only(self, agent_app, app):
        """An agent_registry row with user_id='' can ONLY be rotated by admin.

        Non-admin sessions get 403 because require_owner_or_admin compares
        the session's user_id against an empty string (owner match fails)
        and the session is not admin.
        """
        # Register an agent with the default user_id="" (admin-only).
        cid, _token = await _register_and_mint(app, user_id="admin")
        r = await app.state.agent_registry.get(cid)
        assert r["user_id"] == ""

        # A non-admin session trying to rotate it must get 403.
        nonadmin_client, _uid = _make_nonadmin_client(
            app, app.state.auth, username="randouser", full_name="Rando",
            password="password123",
        )
        async with nonadmin_client:
            resp = await nonadmin_client.post(
                f"/api/agents/registry/{cid}/rotate-tokens"
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_returns_404(self, agent_app):
        """Rotating a nonexistent identity returns 404."""
        resp = await agent_app.post(
            "/api/agents/registry/no-such-agent-20260101-000000/rotate-tokens"
        )
        assert resp.status_code == 404