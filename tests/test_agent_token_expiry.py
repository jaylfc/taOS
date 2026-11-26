"""Agent registry tokens expire, and a running agent renews across expiry.

An agent holds its token in its environment for a long time, so a token that
lapses must not strand it: the agent presents it to the refresh route (an
expired token counts for a grace period) and receives a fresh one, while the
old token is left alone until its own expiry.
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import (
    AGENT_TOKEN_REFRESH_GRACE_SECONDS,
    AGENT_TOKEN_TTL_SECONDS,
    mint_registry_token,
    verify_registry_token,
)



def _keys(app):
    return app.state.agent_registry_keypair


def test_agent_token_carries_exp_but_human_and_service_follow_their_rules():
    from tinyagentos.agent_registry_store import load_or_create_signing_keypair
    import tempfile, pathlib
    priv, pub = load_or_create_signing_keypair(pathlib.Path(tempfile.mkdtemp()))
    now = int(time.time())
    a = verify_registry_token(mint_registry_token("a", priv, iat=now), pub)
    assert a["exp"] == now + AGENT_TOKEN_TTL_SECONDS
    h = verify_registry_token(mint_registry_token("h", priv, principal_type="human"), pub)
    assert "exp" not in h
    s = verify_registry_token(
        mint_registry_token("s", priv, principal_type="service", iat=now, exp=now + 60), pub)
    assert s["exp"] == now + 60


def test_an_expired_agent_token_no_longer_verifies():
    from tinyagentos.agent_registry_store import load_or_create_signing_keypair
    import tempfile, pathlib
    priv, pub = load_or_create_signing_keypair(pathlib.Path(tempfile.mkdtemp()))
    old = int(time.time()) - AGENT_TOKEN_TTL_SECONDS - 10
    tok = mint_registry_token("a", priv, iat=old)
    with pytest.raises(ValueError):
        verify_registry_token(tok, pub)


async def _agent(app, uid):
    rec = await app.state.agent_registry.register(framework="none", display_name="Runner", user_id=uid)
    return rec["canonical_id"]


async def _bare(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
class TestRefreshAcrossExpiry:
    async def test_running_agent_renews_after_its_token_lapses(self, feeds_client):
        app = feeds_client._app
        cid = await _agent(app, feeds_client._test_admin_uid)
        priv, pub = _keys(app)
        # a token that lapsed an hour ago, inside the grace period
        iat = int(time.time()) - AGENT_TOKEN_TTL_SECONDS - 3600
        lapsed = mint_registry_token(cid, priv, iat=iat)
        async with await _bare(app) as bare:
            # the lapsed token is refused on an ordinary route ...
            r = await bare.post("/api/a2a/bus/send", json={"thread": "t", "body": "x"},
                                headers={"Authorization": f"Bearer {lapsed}"})
            assert r.status_code in (401, 403)
            # ... but renews at the refresh route
            r = await bare.post("/api/agents/registry/refresh-token",
                                headers={"Authorization": f"Bearer {lapsed}"})
            assert r.status_code == 200, r.text
            fresh = r.json()["token"]
        claims = verify_registry_token(fresh, pub)
        assert claims["sub"] == cid
        assert claims["exp"] > time.time() + AGENT_TOKEN_TTL_SECONDS - 60
        assert r.json()["expires_at"] == claims["exp"]

    async def test_refresh_before_expiry_leaves_the_old_token_working(self, feeds_client):
        app = feeds_client._app
        cid = await _agent(app, feeds_client._test_admin_uid)
        priv, pub = _keys(app)
        old = mint_registry_token(cid, priv, iat=int(time.time()) - 5)
        async with await _bare(app) as bare:
            r = await bare.post("/api/agents/registry/refresh-token",
                                headers={"Authorization": f"Bearer {old}"})
            assert r.status_code == 200
        fresh = r.json()["token"]
        assert fresh != old
        # no cutoff moved: the token the agent is still running with stays valid
        rec = await app.state.agent_registry.get(cid)
        assert not rec.get("token_min_iat")
        verify_registry_token(old, pub)
        verify_registry_token(fresh, pub)

    async def test_past_the_grace_period_it_cannot_renew(self, feeds_client):
        app = feeds_client._app
        cid = await _agent(app, feeds_client._test_admin_uid)
        priv, _pub = _keys(app)
        iat = int(time.time()) - AGENT_TOKEN_TTL_SECONDS - AGENT_TOKEN_REFRESH_GRACE_SECONDS - 60
        dead = mint_registry_token(cid, priv, iat=iat)
        async with await _bare(app) as bare:
            r = await bare.post("/api/agents/registry/refresh-token",
                                headers={"Authorization": f"Bearer {dead}"})
        assert r.status_code == 401

    async def test_superseded_suspended_and_foreign_tokens_cannot_renew(self, feeds_client):
        app = feeds_client._app
        reg = app.state.agent_registry
        cid = await _agent(app, feeds_client._test_admin_uid)
        priv, _pub = _keys(app)
        tok = mint_registry_token(cid, priv, iat=int(time.time()) - 100)
        async with await _bare(app) as bare:
            h = {"Authorization": f"Bearer {tok}"}
            await reg.bump_token_min_iat(cid, int(time.time()))
            assert (await bare.post("/api/agents/registry/refresh-token", headers=h)).status_code == 401
            cid2 = await _agent(app, feeds_client._test_admin_uid)
            tok2 = mint_registry_token(cid2, priv, iat=int(time.time()) - 100)
            await reg.set_status(cid2, "suspended")
            assert (await bare.post("/api/agents/registry/refresh-token",
                                    headers={"Authorization": f"Bearer {tok2}"})).status_code == 403
            # a token signed by another key
            from tinyagentos.agent_registry_store import load_or_create_signing_keypair
            import tempfile, pathlib
            other, _ = load_or_create_signing_keypair(pathlib.Path(tempfile.mkdtemp()))
            forged = mint_registry_token(cid, other)
            assert (await bare.post("/api/agents/registry/refresh-token",
                                    headers={"Authorization": f"Bearer {forged}"})).status_code == 401
            # a human or service token is not an agent token
            svc = mint_registry_token("svc", priv, principal_type="service",
                                      iat=int(time.time()), exp=int(time.time()) + 60)
            assert (await bare.post("/api/agents/registry/refresh-token",
                                    headers={"Authorization": f"Bearer {svc}"})).status_code in (401, 403)
            assert (await bare.post("/api/agents/registry/refresh-token")).status_code in (401, 403)

    async def test_rotation_mints_a_token_that_also_expires(self, feeds_client):
        app = feeds_client._app
        cid = await _agent(app, feeds_client._test_admin_uid)
        r = await feeds_client.post(f"/api/agents/registry/{cid}/rotate-tokens")
        assert r.status_code == 200, r.text
        tok = r.json().get("token") or r.json().get("tokens", {}).get("token")
        claims = verify_registry_token(tok, _keys(app)[1])
        assert "exp" in claims
