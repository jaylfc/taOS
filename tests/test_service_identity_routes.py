"""Service-principal tokens through the REAL middleware and routes (tsk-x44f46).

Every test here drives an HTTP route (or the real application lifespan), never
a helper in isolation.  The controller's service row is given every grant the
route under test checks for, so the only thing that can refuse the request is
the principal KIND on the token -- a missing grant must not be what makes a
test pass.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.agent_registry_store import (
    mint_registry_token,
    verify_registry_token,
)
from tinyagentos.app import create_app
from tinyagentos.controller_service_identity import (
    controller_token_path,
    ensure_controller_service_identity,
)

from test_a2a_bus_agent_auth import _BUS_PATCH, _mock_bus_post


_STORES = ("agent_registry", "agent_grants", "metrics", "auth_requests", "project_store")


def _bare(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def svc(app, tmp_data_dir):
    """An app with an owner, the controller service identity and its token.

    The service row also holds the grants the routes below check, so a 4xx is
    attributable to the token kind alone.
    """
    for attr in _STORES:
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = app.state.auth.find_user("admin")["id"]
    session = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    priv, _pub = app.state.agent_registry_keypair
    rec = await ensure_controller_service_identity(
        registry=app.state.agent_registry,
        grants=app.state.agent_grants,
        data_dir=tmp_data_dir,
        signing_key_pem=priv,
        user_id=uid,
    )
    cid = rec["canonical_id"]
    grants = app.state.agent_grants
    for scope in ("a2a_send", "a2a_receive", "cluster_read", "agents:read"):
        await grants.add_grant(cid, scope)
    await grants.add_grant(cid, "project_tasks", project_id="p1")
    token = controller_token_path(tmp_data_dir).read_text().strip()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as admin:
        admin._app = app
        admin._svc = {"cid": cid, "token": token, "uid": uid, "dir": tmp_data_dir}
        yield admin

    for attr in _STORES:
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestServiceTokenRefusedByEveryReader:
    async def test_bus_send_refused(self, svc):
        ctx, _client = _mock_bus_post({"id": 1})
        with patch(_BUS_PATCH, return_value=ctx):
            async with _bare(svc._app) as bare:
                resp = await bare.post(
                    "/api/a2a/bus/send",
                    json={"thread": "general", "body": "hi"},
                    headers=_bearer(svc._svc["token"]),
                )
        assert resp.status_code in (401, 403)

    async def test_self_rotate_refused_and_cutoff_untouched(self, svc):
        """agent_token_auth check_agent_identity_claims: a service token must
        not pass as the agent whose canonical_id it names."""
        cid = svc._svc["cid"]
        before = (await svc._app.state.agent_registry.get(cid))["token_min_iat"]
        async with _bare(svc._app) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid}/rotate-tokens",
                headers=_bearer(svc._svc["token"]),
            )
        assert resp.status_code == 404, resp.text
        assert "token" not in resp.text
        after = (await svc._app.state.agent_registry.get(cid))["token_min_iat"]
        assert after == before

    async def test_scope_request_proof_refused(self, svc):
        """_classify_proof must not record a service token as proof of an
        agent identity."""
        async with _bare(svc._app) as bare:
            resp = await bare.post(
                "/api/agents/auth-requests",
                json={
                    "identity_claim": "service:controller",
                    "framework": "hermes",
                    "requested_scopes": ["a2a_send"],
                },
                headers=_bearer(svc._svc["token"]),
            )
        if resp.status_code == 200:
            row = await svc._app.state.auth_requests.get(resp.json()["request_id"])
            assert not row.get("proven_canonical_id")
        else:
            assert resp.status_code in (401, 403)

    async def test_project_grants_enumeration_refused(self, svc):
        """check_agent_project_grants (agent_token_auth) is reached by the
        tasks aggregate route."""
        async with _bare(svc._app) as bare:
            resp = await bare.get(
                "/api/projects/tasks/aggregate",
                headers=_bearer(svc._svc["token"]),
            )
        assert resp.status_code in (401, 403), resp.text

    async def test_exempt_get_not_promoted_by_middleware(self, svc):
        """auth_middleware exempt-GET branch: the service row holds
        agents:read and cluster_read, yet must see exactly the anonymous view."""
        async with _bare(svc._app) as bare:
            anon = await bare.get("/api/cluster/workers")
            promoted = await bare.get(
                "/api/cluster/workers", headers=_bearer(svc._svc["token"])
            )
        assert promoted.status_code == anon.status_code
        assert promoted.text == anon.text

    async def test_forged_kind_on_native_agent_row_refused(self, svc):
        """auth_middleware allowlisted branch: a token for the native agent's
        row that claims the service kind is not the native agent."""
        from tinyagentos.native_agent_identity import ensure_native_agent_identity

        app = svc._app
        priv, _ = app.state.agent_registry_keypair
        native = await ensure_native_agent_identity(
            registry=app.state.agent_registry,
            grants=app.state.agent_grants,
            data_dir=svc._svc["dir"],
            signing_key_pem=priv,
            user_id=svc._svc["uid"],
        )
        genuine = mint_registry_token(
            native["canonical_id"], priv, user_id=svc._svc["uid"], framework="taos"
        )
        forged = mint_registry_token(
            native["canonical_id"], priv, principal_type="service"
        )
        async with _bare(app) as bare:
            ok = await bare.post("/api/desktop/command", json={}, headers=_bearer(genuine))
            bad = await bare.post("/api/desktop/command", json={}, headers=_bearer(forged))
        assert ok.status_code != 403, "control: the genuine native token must pass"
        assert bad.status_code == 403

    async def test_forged_kind_on_deployed_agent_row_refused(self, svc):
        """auth_middleware deployed-runtime branch."""
        app = svc._app
        priv, _ = app.state.agent_registry_keypair
        rec = await app.state.agent_registry.register(
            framework="openclaw", display_name="Dep", origin="taos-deployed",
            handle="@dep", user_id=svc._svc["uid"],
        )
        app.state.config.agents.append(
            {"name": "dep", "registry_canonical_id": rec["canonical_id"]}
        )
        genuine = mint_registry_token(
            rec["canonical_id"], priv, user_id=svc._svc["uid"], framework="openclaw"
        )
        forged = mint_registry_token(rec["canonical_id"], priv, principal_type="service")
        async with _bare(app) as bare:
            ok = await bare.post("/api/trace", json={}, headers=_bearer(genuine))
            bad = await bare.post("/api/trace", json={}, headers=_bearer(forged))
        assert ok.status_code != 401, "control: the genuine agent token must pass"
        assert bad.status_code == 401


@pytest.mark.asyncio
class TestNobodyElseMintsOrImpersonatesService:
    async def test_admin_cannot_rotate_controller_into_an_agent_token(self, svc):
        cid = svc._svc["cid"]
        resp = await svc.post(f"/api/agents/registry/{cid}/rotate-tokens")
        assert resp.status_code in (403, 404, 409), resp.text
        assert "token" not in resp.json()

    async def test_mint_internal_adopt_refused_for_the_controller_row(self, svc):
        resp = await svc.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "service:controller", "slug": "svc", "scopes": [],
                  "adopt": True},
        )
        assert resp.status_code in (403, 409), resp.text
        assert "token" not in resp.json()

    async def test_mint_route_ignores_a_requested_principal_type(self, svc):
        resp = await svc.post(
            "/api/agents/registry/register",
            json={"framework": "hermes", "display_name": "x",
                  "principal_type": "service", "scope": "records:write"},
        )
        assert resp.status_code in (200, 201, 422), resp.text
        if resp.status_code in (200, 201):
            _priv, pub = svc._app.state.agent_registry_keypair
            claims = verify_registry_token(resp.json()["token"], pub)
            assert claims["principal_type"] == "agent"
            assert "scope" not in claims

    async def test_human_assertion_is_human_kind(self, svc):
        resp = await svc.post("/api/a2a/bus/human-assertion")
        _priv, pub = svc._app.state.agent_registry_keypair
        claims = verify_registry_token(resp.json()["assertion"], pub)
        assert claims["principal_type"] == "human"
        assert "scope" not in claims

    async def test_agent_token_cannot_act_as_the_service_row(self, svc):
        """An agent-kind token minted for the controller's canonical_id (what a
        rotation would have produced) must not authenticate as anything: the
        row is a service row, so no agent path accepts it."""
        app = svc._app
        priv, _ = app.state.agent_registry_keypair
        as_agent = mint_registry_token(
            svc._svc["cid"], priv, user_id=svc._svc["uid"], framework="taos-controller"
        )
        ctx, _c = _mock_bus_post({"id": 1})
        with patch(_BUS_PATCH, return_value=ctx):
            async with _bare(app) as bare:
                resp = await bare.post(
                    "/api/a2a/bus/send",
                    json={"thread": "general", "body": "hi"},
                    headers=_bearer(as_agent),
                )
        assert resp.status_code in (401, 403)


@pytest.mark.asyncio
class TestReservedHandleThroughRoutes:
    async def test_register_route_refuses_the_service_namespace(self, svc):
        for h in ("service:controller", "@Service:Controller", "service:other"):
            resp = await svc.post(
                "/api/agents/registry/register",
                json={"framework": "hermes", "display_name": "squat", "handle": h,
                      "origin": "external-selfjoin"},
            )
            assert resp.status_code == 422, (h, resp.text)

    async def test_patch_and_mint_internal_refuse_it_too(self, svc):
        ok = await svc.post(
            "/api/agents/registry/register",
            json={"framework": "hermes", "display_name": "fine", "handle": "@fine",
                  "origin": "external-selfjoin"},
        )
        cid = ok.json()["canonical_id"]
        patched = await svc.patch(
            f"/api/agents/registry/{cid}", json={"handle": "service:controller"}
        )
        assert patched.status_code == 409, patched.text
        minted = await svc.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "service:x", "slug": "x", "scopes": []},
        )
        assert minted.status_code == 422, minted.text


@pytest.mark.asyncio
class TestHumanTokenUnchanged:
    async def test_human_assertion_sends_on_the_bus(self, svc):
        token = (await svc.post("/api/a2a/bus/human-assertion")).json()["assertion"]
        ctx, _c = _mock_bus_post({"id": 1, "from": "@admin", "thread": "general"})
        with patch(_BUS_PATCH, return_value=ctx):
            async with _bare(svc._app) as bare:
                resp = await bare.post(
                    "/api/a2a/bus/send",
                    json={"thread": "general", "body": "hello"},
                    headers=_bearer(token),
                )
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
class TestTokenNeverLeaks:
    async def test_not_in_logs_responses_or_state(self, svc, caplog):
        token = svc._svc["token"]
        sig = token.split(".")[2]
        caplog.set_level(logging.DEBUG)
        async with _bare(svc._app) as bare:
            responses = [
                await bare.post(
                    "/api/a2a/bus/send",
                    json={"thread": "t", "body": "b"},
                    headers=_bearer(token),
                ),
                await bare.post(
                    f"/api/agents/registry/{svc._svc['cid']}/rotate-tokens",
                    headers=_bearer(token),
                ),
                await bare.get("/api/health"),
            ]
        # A failed write and a failed rotation must not echo it either.
        from tinyagentos import controller_service_identity as mod

        with patch.object(mod, "atomic_write_bytes", side_effect=OSError("disk")):
            await mod.rotate_controller_service_token(
                registry=svc._app.state.agent_registry,
                data_dir=svc._svc["dir"],
                signing_key_pem=svc._app.state.agent_registry_keypair[0],
            )
        blob = caplog.text + "".join(r.text for r in responses)
        assert token not in blob
        assert sig not in blob
        assert token not in repr(vars(svc._app.state).get("config", ""))
