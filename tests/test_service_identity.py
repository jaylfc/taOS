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
    async def test_check_agent_scope_refuses_service_token(self, token_app):
        priv, _pub = token_app.state.agent_registry_keypair
        token = mint_registry_token("service-cid", priv, principal_type="service")
        req = _FakeRequest(token_app, token)
        with pytest.raises(HTTPException) as exc:
            await check_agent_scope(req, "records:write")
        assert exc.value.status_code == 401

    async def test_check_agent_scope_still_returns_none_for_human_token(self, token_app):
        priv, _pub = token_app.state.agent_registry_keypair
        token = mint_registry_token("some-user", priv, principal_type="human")
        req = _FakeRequest(token_app, token)
        assert await check_agent_scope(req, "a2a_send") is None

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


# ---------------------------------------------------------------------------
# tsk-x44f46: claims, renewal, atomic write, no duplicate row, on_behalf_of
# ---------------------------------------------------------------------------

import asyncio
import logging
import sqlite3
import time

from tinyagentos import controller_service_identity as csi


def _svc_token(cid, priv, *, exp, iat=None, scope="records:write"):
    return mint_registry_token(
        cid, priv, principal_type="service", scope=scope, iat=iat, exp=exp
    )


@pytest.mark.asyncio
class TestServiceTokenClaims:
    async def test_scope_claim_is_space_separated_and_exp_is_set(self, tmp_path):
        _r, _g, data_dir, keypair, rec = await _ensure(tmp_path)
        claims = verify_registry_token(
            csi.controller_token_path(data_dir).read_text(), keypair[1]
        )
        assert claims["scope"] == "records:write"
        assert claims["exp"] - claims["iat"] == csi.TOKEN_TTL_SECONDS
        assert claims["principal_type"] == "service"

    async def test_expired_service_token_fails_verification(self, tmp_path):
        _r, _g, _d, keypair = await _stores(tmp_path)
        token = _svc_token("c", keypair[0], exp=int(time.time()) - 5)
        with pytest.raises(ValueError, match="expired"):
            verify_registry_token(token, keypair[1])

    async def test_tokens_without_exp_are_unaffected(self, tmp_path):
        _r, _g, _d, keypair = await _stores(tmp_path)
        token = mint_registry_token("a", keypair[0], user_id="u", framework="f")
        assert "exp" not in verify_registry_token(token, keypair[1])


@pytest.mark.asyncio
class TestAtomicTokenWriteAndStatusLookup:
    async def test_partial_token_file_is_replaced_not_trusted(self, tmp_path):
        registry, grants, data_dir, keypair, rec = await _ensure(tmp_path)
        path = csi.controller_token_path(data_dir)
        path.write_text("eyJhbGciOi")  # what a crash mid-write leaves behind
        await csi.ensure_controller_service_identity(
            registry=registry, grants=grants, data_dir=data_dir,
            signing_key_pem=keypair[0], user_id="user-1",
        )
        claims = verify_registry_token(path.read_text(), keypair[1])
        assert claims["sub"] == rec["canonical_id"]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    async def test_legacy_token_without_exp_or_scope_is_renewed(self, tmp_path):
        registry, grants, data_dir, keypair, rec = await _ensure(tmp_path)
        path = csi.controller_token_path(data_dir)
        legacy = mint_registry_token(
            rec["canonical_id"], keypair[0], principal_type="service",
            iat=int(time.time()) - 10,
        )
        path.write_text(legacy)
        await csi.ensure_controller_service_identity(
            registry=registry, grants=grants, data_dir=data_dir,
            signing_key_pem=keypair[0], user_id="user-1",
        )
        claims = verify_registry_token(path.read_text(), keypair[1])
        assert claims["scope"] == "records:write" and claims["exp"]

    async def test_no_temp_files_left_and_mode_0600_after_rotation(self, tmp_path):
        registry, _g, data_dir, keypair, rec = await _ensure(tmp_path)
        await csi.rotate_controller_service_token(
            registry=registry, data_dir=data_dir, signing_key_pem=keypair[0]
        )
        names = sorted(p.name for p in csi.controller_token_path(data_dir).parent.iterdir())
        assert names == ["taos_controller_token"]
        assert stat.S_IMODE(csi.controller_token_path(data_dir).stat().st_mode) == 0o600

    @pytest.mark.parametrize("status", ["suspended", "revoked"])
    async def test_non_active_controller_row_is_never_duplicated(self, tmp_path, status):
        registry, grants, data_dir, keypair, rec = await _ensure(tmp_path)
        await registry.set_status(rec["canonical_id"], status)
        csi.controller_token_path(data_dir).unlink()
        again = await csi.ensure_controller_service_identity(
            registry=registry, grants=grants, data_dir=data_dir,
            signing_key_pem=keypair[0], user_id="user-1",
        )
        rows = [r for r in await registry.list_all() if r["origin"] == "taos-controller"]
        assert len(rows) == 1 and rows[0]["status"] == status
        assert again is None
        assert not csi.controller_token_path(data_dir).exists()


@pytest.mark.asyncio
class TestRenewalThroughRealLifespan:
    async def _first_boot(self, work):
        async with _lifespan(work) as app:
            cid = next(
                r["canonical_id"] for r in await app.state.agent_registry.list_all()
                if r["origin"] == "taos-controller"
            )
            priv = app.state.agent_registry_keypair[0]
            pub = app.state.agent_registry_keypair[1]
        return cid, priv, pub

    async def test_near_expiry_token_is_renewed_at_start(self, tmp_path):
        work = tmp_path / "w"
        work.mkdir()
        cid, priv, pub = await self._first_boot(work)
        path = csi.controller_token_path(work)
        old = _svc_token(cid, priv, exp=int(time.time()) + 60,
                         iat=int(time.time()) - 5)
        path.write_text(old)
        async with _lifespan(work) as app:
            new = path.read_text()
            assert new != old
            claims = verify_registry_token(new, pub)
            assert claims["exp"] - time.time() > csi.RENEW_BEFORE_SECONDS
            row = await app.state.agent_registry.get(cid)
            assert verify_registry_token(old, pub)["iat"] < row["token_min_iat"]
            assert len([r for r in await app.state.agent_registry.list_all()
                        if r["origin"] == "taos-controller"]) == 1

    async def test_expired_token_is_renewed_at_start(self, tmp_path):
        work = tmp_path / "w"
        work.mkdir()
        cid, priv, pub = await self._first_boot(work)
        path = csi.controller_token_path(work)
        path.write_text(_svc_token(cid, priv, exp=int(time.time()) - 100,
                                   iat=int(time.time()) - 5000))
        async with _lifespan(work):
            claims = verify_registry_token(path.read_text(), pub)
            assert claims["exp"] > time.time()

    async def test_failed_rotation_keeps_old_token_and_start_is_not_blocked(
        self, tmp_path, monkeypatch
    ):
        work = tmp_path / "w"
        work.mkdir()
        cid, priv, pub = await self._first_boot(work)
        path = csi.controller_token_path(work)
        old = _svc_token(cid, priv, exp=int(time.time()) + 60,
                         iat=int(time.time()) - 5)
        path.write_text(old)

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(csi, "atomic_write_bytes", _boom)
        async with _lifespan(work) as app:
            assert app.state._startup_complete is True
            assert path.read_text() == old
            row = await app.state.agent_registry.get(cid)
            # The cutoff did not move, so the old token is still honoured.
            assert verify_registry_token(old, pub)["iat"] >= (row["token_min_iat"] or 0)

    async def test_ensure_raising_does_not_block_start(self, tmp_path, monkeypatch):
        async def _boom(**_k):
            raise RuntimeError("registry down")

        monkeypatch.setattr(csi, "ensure_controller_service_identity", _boom)
        work = tmp_path / "w"
        work.mkdir()
        async with _lifespan(work) as app:
            assert app.state._startup_complete is True

    async def test_running_controller_renews_before_expiry(self, tmp_path, monkeypatch):
        work = tmp_path / "w"
        work.mkdir()
        cid, priv, pub = await self._first_boot(work)
        monkeypatch.setattr(csi, "RENEWAL_INTERVAL_SECONDS", 0.05)
        path = csi.controller_token_path(work)
        async with _lifespan(work):
            old = _svc_token(cid, priv, exp=int(time.time()) + 60,
                             iat=int(time.time()) - 5)
            path.write_text(old)
            for _ in range(100):
                await asyncio.sleep(0.1)
                if path.read_text() != old:
                    break
            assert path.read_text() != old, "the running controller never renewed"
            assert verify_registry_token(path.read_text(), pub)["exp"] > time.time() + 3600

    async def test_grants_migration_is_additive_row_count_parity(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        registry, grants, _d, _k = await _stores(source)
        agent = await registry.register(
            framework="openclaw", display_name="Existing",
            origin="external-selfjoin", handle="@existing",
        )
        await registry.set_status(agent["canonical_id"], "active")
        for scope in ("a2a_send", "memory_read", "a2a_receive"):
            await grants.add_grant(agent["canonical_id"], scope)
        await _close(registry, grants)
        work = tmp_path / "work"
        shutil.copytree(source, work)

        def rows():
            con = sqlite3.connect(work / "agent_grants.db")
            try:
                return con.execute(
                    "SELECT canonical_id, scope, project_id, tier, expires_at "
                    "FROM agent_grants ORDER BY canonical_id, scope"
                ).fetchall()
            finally:
                con.close()

        before = rows()
        assert len(before) == 3
        async with _lifespan(work):
            pass
        after = rows()
        mine = agent["canonical_id"]
        # Every pre-existing row is still there, byte for byte (row-count parity
        # for the existing agent), and the only controller row added is the
        # one records:write grant.  The native agent's own baseline grants are
        # the OS's other first-boot identity and are not part of this change.
        assert [r for r in after if r[0] == mine] == before
        assert [r[1] for r in after if r[0].startswith("taos-controller-")] == ["records:write"]
        assert set(before) <= set(after)


@pytest.mark.asyncio
class TestNoLeaksInLogs:
    async def test_mint_renew_and_failure_never_log_the_token(self, tmp_path, caplog, monkeypatch):
        caplog.set_level(logging.DEBUG)
        registry, grants, data_dir, keypair, rec = await _ensure(tmp_path)
        first = csi.controller_token_path(data_dir).read_text()
        second = await csi.rotate_controller_service_token(
            registry=registry, data_dir=data_dir, signing_key_pem=keypair[0]
        )
        monkeypatch.setattr(
            csi, "atomic_write_bytes", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
        )
        third = await csi.rotate_controller_service_token(
            registry=registry, data_dir=data_dir, signing_key_pem=keypair[0]
        )
        assert third is None
        for tok in (first, second):
            assert tok not in caplog.text
            assert tok.split(".")[2] not in caplog.text
            assert tok.split(".")[1] not in caplog.text


class TestOnBehalfOf:
    def test_record_names_the_person(self):
        out = csi.controller_record({"kind": "history", "body": "x"}, on_behalf_of="user-9")
        assert out["on_behalf_of"] == "user-9"
        assert out["kind"] == "history"

    def test_no_person_means_no_field_and_input_is_not_mutated(self):
        rec = {"kind": "history"}
        out = csi.controller_record(rec)
        assert "on_behalf_of" not in out and rec == {"kind": "history"}
