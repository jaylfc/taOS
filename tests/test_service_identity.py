"""Service identity for the controller, plus the agreed scope names.

These tests drive the real function (``ensure_controller_service_identity``)
and the real application lifespan over a copy of an existing install's data
directory -- not a helper or a copy of the logic.

RED-FIRST: against origin/dev the suite fails because the controller registers
no service identity, ``check_agent_scope`` accepts a service token as an agent,
and ``VALID_SCOPES`` lacks the records/blobs/bus/secrets/archive names.
"""
from __future__ import annotations

import shutil
import stat

import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from fastapi import HTTPException

from tinyagentos.app import create_app
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
    verify_registry_token,
)
from tinyagentos.agent_token_auth import check_agent_scope, check_human_identity
from tinyagentos.routes.agent_auth_requests import VALID_SCOPES

AGREED_SCOPES = {
    "records:write",
    "records:read",
    "blobs:write",
    "blobs:read",
    "bus:send",
    "bus:read",
    "bus:inbox",
    "secrets:reveal",
    "archive:admin",
}


class _FakeRequest:
    """Minimal stand-in for a starlette Request: the auth helpers only touch
    ``.headers.get`` and ``.app.state`` on the object."""

    def __init__(self, app, token: str | None = None):
        self.app = app
        self.headers = {}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"


async def _stores(tmp_path):
    """Registry + grants + signing key over one tmp data_dir."""
    registry = AgentRegistryStore(tmp_path / "agent_registry.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "agent_grants.db")
    await grants.init()
    keypair = load_or_create_signing_keypair(tmp_path)
    return registry, grants, tmp_path, keypair


async def _close(*stores):
    for store in stores:
        if getattr(store, "_db", None) is not None:
            await store.close()


@pytest_asyncio.fixture
async def token_app(app):
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    yield app
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


async def _ensure(tmp_path, user_id="user-1"):
    from tinyagentos.controller_service_identity import (
        ensure_controller_service_identity,
    )

    registry, grants, data_dir, keypair = await _stores(tmp_path)
    rec = await ensure_controller_service_identity(
        registry=registry,
        grants=grants,
        data_dir=data_dir,
        signing_key_pem=keypair[0],
        user_id=user_id,
    )
    return registry, grants, data_dir, keypair, rec


@asynccontextmanager
async def _lifespan(data_dir):
    """Boot the real application lifespan over *data_dir* and yield the app.

    Uses FastAPI's own lifespan context manager (the same code uvicorn runs),
    so the controller service identity is ensured exactly as it would be at a
    real boot.  Stores stay open for the duration of the context so callers can
    inspect app.state directly; on exit the lifespan teardown closes them.
    """
    app = create_app(data_dir=data_dir)
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("owner", "Owner", "", "password01")
    async with app.router.lifespan_context(app):
        yield app


# ---------------------------------------------------------------------------
# mint_registry_token accepts principal_type="service"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMintServiceToken:
    async def test_mint_service_token_principal_type(self, tmp_path):
        _registry, _grants, _data_dir, keypair = await _stores(tmp_path)
        token = mint_registry_token("service-cid", keypair[0], principal_type="service")
        claims = verify_registry_token(token, keypair[1])
        assert claims["sub"] == "service-cid"
        assert claims["principal_type"] == "service"
        # A service token carries no agent-only user_id/framework claims.
        assert "user_id" not in claims
        assert "framework" not in claims

    async def test_agent_and_human_tokens_are_unchanged(self, tmp_path):
        _registry, _grants, _data_dir, keypair = await _stores(tmp_path)
        agent = mint_registry_token(
            "agent-cid", keypair[0], user_id="u", framework="grok"
        )
        human = mint_registry_token("u", keypair[0], principal_type="human")
        assert verify_registry_token(agent, keypair[1])["user_id"] == "u"
        assert verify_registry_token(agent, keypair[1])["framework"] == "grok"
        assert verify_registry_token(human, keypair[1])["principal_type"] == "human"
        assert verify_registry_token(human, keypair[1])["sub"] == "u"


# ---------------------------------------------------------------------------
# A service token is not accepted where an agent or human token is required
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestServiceTokenNotAcceptedAsAgent:
    async def test_check_agent_scope_returns_none_for_service_token(self, token_app):
        priv, _pub = token_app.state.agent_registry_keypair
        token = mint_registry_token("service-cid", priv, principal_type="service")
        req = _FakeRequest(token_app, token)
        assert await check_agent_scope(req, "records:write") is None

    async def test_check_human_identity_401_for_service_token(self, token_app):
        priv, _pub = token_app.state.agent_registry_keypair
        token = mint_registry_token("service-cid", priv, principal_type="service")
        req = _FakeRequest(token_app, token)
        with pytest.raises(HTTPException) as exc:
            await check_human_identity(req)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# The grant vocabulary carries the agreed scope names
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestScopeVocabulary:
    async def test_agreed_scope_names_are_in_valid_scopes(self):
        missing = sorted(AGREED_SCOPES - VALID_SCOPES)
        assert not missing, f"missing scopes from VALID_SCOPES: {missing}"


# ---------------------------------------------------------------------------
# Controller service identity (real function, real stores)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestControllerServiceIdentity:
    async def test_creates_service_principal_owned_by_owner(self, tmp_path):
        registry, grants, data_dir, keypair, rec = await _ensure(tmp_path)
        assert rec is not None
        assert rec["status"] == "active"
        assert rec["origin"] == "taos-controller"
        assert rec["handle"] == "service:controller"
        assert rec["user_id"] == "user-1"
        assert rec["install_id"]
        assert rec["canonical_id"].startswith("taos-controller-")

    async def test_service_holds_records_write_only(self, tmp_path):
        registry, grants, data_dir, keypair, rec = await _ensure(tmp_path)
        scopes = {g["scope"] for g in await grants.list_grants(rec["canonical_id"])}
        assert scopes == {"records:write"}

    async def test_token_written_0600_in_secrets_area_and_verifies(self, tmp_path):
        registry, grants, data_dir, keypair, rec = await _ensure(tmp_path)
        from tinyagentos.controller_service_identity import controller_token_path

        path = controller_token_path(data_dir)
        assert path.exists()
        assert path.parent.name == ".secrets"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

        claims = verify_registry_token(path.read_text(), keypair[1])
        assert claims["sub"] == rec["canonical_id"]
        assert claims["principal_type"] == "service"

    async def test_idempotent_across_restarts(self, tmp_path):
        registry, grants, data_dir, keypair, first = await _ensure(tmp_path)
        token_first = (data_dir / ".secrets" / "taos_controller_token").read_text()

        second_registry, second_grants, second_data_dir, _keypair2, second = await (
            _ensure(tmp_path)
        )

        assert second["canonical_id"] == first["canonical_id"]
        service_rows = [
            r
            for r in await registry.list_for_install(first["install_id"], status="active")
            if r.get("origin") == "taos-controller"
        ]
        assert len(service_rows) == 1
        assert (data_dir / ".secrets" / "taos_controller_token").read_text() == token_first

    async def test_deferred_without_owner(self, tmp_path):
        registry, grants, data_dir, _keypair, rec = await _ensure(tmp_path, user_id="")
        assert rec is None
        assert await registry.list_all() == []
        assert not (data_dir / ".secrets" / "taos_controller_token").exists()

    async def test_deferred_without_install_id(self, tmp_path, monkeypatch):
        import tinyagentos.controller_service_identity as mod

        monkeypatch.setattr("tinyagentos.controller_service_identity.read_install_id", lambda _d: "")
        registry, grants, data_dir, _keypair, rec = await _ensure(tmp_path, user_id="user-1")
        assert rec is None
        assert await registry.list_all() == []
        assert not (data_dir / ".secrets" / "taos_controller_token").exists()


# ---------------------------------------------------------------------------
# Real lifespan over a copy of an existing install's data directory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestServiceIdentityOverRealLifespan:
    async def test_service_principal_created_once_over_two_restarts(self, tmp_path):
        # Seed an existing install: registry + grants stores created by the
        # current release, with an existing agent and its grants intact.
        source = tmp_path / "source"
        source.mkdir()
        registry, grants, _data_dir, _keypair = await _stores(source)
        agent = await registry.register(
            framework="openclaw", display_name="Existing",
            origin="external-selfjoin", handle="@existing",
        )
        await registry.set_status(agent["canonical_id"], "active")
        await grants.add_grant(agent["canonical_id"], "a2a_send")
        await grants.add_grant(agent["canonical_id"], "memory_read")
        existing_grants = {
            g["scope"] for g in await grants.list_grants(agent["canonical_id"])
        }
        await _close(registry, grants)

        work = tmp_path / "work"
        shutil.copytree(source, work)

        # First boot: the service principal is created, owned by the install owner.
        async with _lifespan(work) as app1:
            reg1 = app1.state.agent_registry
            gr1 = app1.state.agent_grants
            service_rows = [
                r for r in await reg1.list_all() if r.get("origin") == "taos-controller"
            ]
            assert len(service_rows) == 1
            rec = service_rows[0]
            assert rec["handle"] == "service:controller"
            assert rec["user_id"] == app1.state.auth.get_primary_user()["id"]
            scopes = {g["scope"] for g in await gr1.list_grants(rec["canonical_id"])}
            assert scopes == {"records:write"}
            # The token verifies as a service principal and lives in the secrets area.
            from tinyagentos.controller_service_identity import controller_token_path

            token_path = controller_token_path(work)
            assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
            claims = verify_registry_token(
                token_path.read_text(), app1.state.agent_registry_keypair[1]
            )
            assert claims["principal_type"] == "service"
            assert claims["sub"] == rec["canonical_id"]
            # Existing agent grants are unchanged (row-count parity for that agent).
            after = {g["scope"] for g in await gr1.list_grants(agent["canonical_id"])}
            assert after == existing_grants
            token_first = token_path.read_text()

        # Second boot over the same directory: created exactly once.
        async with _lifespan(work) as app2:
            reg2 = app2.state.agent_registry
            service_rows2 = [
                r for r in await reg2.list_all() if r.get("origin") == "taos-controller"
            ]
            assert len(service_rows2) == 1
            assert service_rows2[0]["canonical_id"] == rec["canonical_id"]
            assert controller_token_path(work).read_text() == token_first
