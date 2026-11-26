"""Who gets which memory shelf grant: the controller, the native agent, a
deployed agent (backfilled on upgrade), and a person who opts in."""
from __future__ import annotations

import pytest

from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
)


async def _stores(tmp_path):
    registry = AgentRegistryStore(tmp_path / "agent_registry.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "agent_grants.db")
    await grants.init()
    return registry, grants, tmp_path, load_or_create_signing_keypair(tmp_path)


async def _shelves(grants, cid, scope="memory:shelf"):
    return sorted(g["shelf"] for g in await grants.list_shelf_grants(cid) if g["scope"] == scope)


@pytest.mark.asyncio
class TestNativeAgentShelfGrant:
    async def test_native_agent_holds_its_shelf_only(self, tmp_path):
        from tinyagentos.native_agent_identity import (
            NATIVE_AGENT_SHELF, ensure_native_agent_identity)
        registry, grants, data_dir, keypair = await _stores(tmp_path)
        rec = await ensure_native_agent_identity(
            registry=registry, grants=grants, data_dir=data_dir,
            signing_key_pem=keypair[0], user_id="user-1")
        assert await _shelves(grants, rec["canonical_id"]) == [NATIVE_AGENT_SHELF]
        assert "memory:shelf" not in {g["scope"] for g in await grants.list_grants(rec["canonical_id"])}


@pytest.mark.asyncio
class TestBackfillAndOptIn:
    async def test_backfill_grants_each_deployed_agent_its_own_shelf(self, tmp_path):
        from tinyagentos.shelf_grants import backfill_agent_shelf_grants
        registry, grants, _d, _k = await _stores(tmp_path)
        a = await registry.register(framework="none", display_name="Alpha", user_id="u")
        b = await registry.register(framework="none", display_name="Beta", user_id="u")
        agents = [
            {"name": "alpha", "registry_canonical_id": a["canonical_id"]},
            {"name": "beta", "registry_canonical_id": b["canonical_id"], "memory_mode": "framework"},
            {"name": "ghost"},  # no registry identity
        ]
        n = await backfill_agent_shelf_grants(grants, registry, agents)
        assert n == 1
        assert await _shelves(grants, a["canonical_id"]) == ["alpha"]
        assert await _shelves(grants, b["canonical_id"]) == []
        # idempotent; and an agent is never handed another agent's shelf
        assert await backfill_agent_shelf_grants(grants, registry, agents) == 0

    async def test_backfill_skips_revoked_identity(self, tmp_path):
        from tinyagentos.shelf_grants import backfill_agent_shelf_grants
        registry, grants, _d, _k = await _stores(tmp_path)
        a = await registry.register(framework="none", display_name="Alpha", user_id="u")
        await registry.revoke(a["canonical_id"])
        await backfill_agent_shelf_grants(
            grants, registry, [{"name": "alpha", "registry_canonical_id": a["canonical_id"]}])
        assert await _shelves(grants, a["canonical_id"]) == []

    async def test_person_opt_in_is_read_only_scope_on_the_agents_shelf(self, tmp_path):
        from tinyagentos.shelf_grants import grant_person_read, revoke_person_read
        _r, grants, _d, _k = await _stores(tmp_path)
        await grant_person_read(grants, "person-1", "alpha")
        assert await _shelves(grants, "person-1", "memory:read") == ["alpha"]
        assert await _shelves(grants, "person-1", "memory:shelf") == []
        assert await revoke_person_read(grants, "person-1", "alpha") is True
        assert await _shelves(grants, "person-1", "memory:read") == []


from tests.test_agent_registry import feeds_client  # noqa: E402,F401


@pytest.mark.asyncio
class TestMemoryReadOptInRoute:
    async def _agent(self, client, name="alpha", owner=None):
        app = client._app
        uid = owner if owner is not None else client._test_admin_uid
        rec = await app.state.agent_registry.register(framework="none", display_name=name, user_id=uid)
        app.state.config.agents = [{"name": name, "registry_canonical_id": rec["canonical_id"]}]
        return rec

    async def test_owner_opts_in_and_out(self, feeds_client):
        await self._agent(feeds_client)
        uid = feeds_client._test_admin_uid
        r = await feeds_client.put("/api/agents/alpha/memory-read")
        assert r.status_code == 200, r.text
        assert r.json() == {"agent": "alpha", "shelf": "alpha", "memory_read": True}
        grants = (await feeds_client.get("/api/agents/registry/grants")).json()["grants"]
        rec = [g for g in grants if g["scope"] == "memory:read"]
        assert [(g["canonical_id"], g["shelf"]) for g in rec] == [(uid, "alpha")]
        r = await feeds_client.delete("/api/agents/alpha/memory-read")
        assert r.status_code == 200 and r.json()["memory_read"] is False
        grants = (await feeds_client.get("/api/agents/registry/grants")).json()["grants"]
        assert not [g for g in grants if g["scope"] == "memory:read"]

    async def test_not_the_owner_is_refused(self, feeds_client):
        await self._agent(feeds_client, owner="someone-else")
        r = await feeds_client.put("/api/agents/alpha/memory-read")
        assert r.status_code == 403
        grants = (await feeds_client.get("/api/agents/registry/grants")).json()["grants"]
        assert not [g for g in grants if g["scope"] == "memory:read"]

    async def test_unknown_agent_is_refused(self, feeds_client):
        r = await feeds_client.put("/api/agents/nobody/memory-read")
        assert r.status_code in (403, 404)


@pytest.mark.asyncio
class TestNativeTokenRenewsBeforeItExpires:
    async def test_native_token_is_replaced_when_near_expiry(self, tmp_path):
        import time
        from tinyagentos.agent_registry_store import AGENT_TOKEN_TTL_SECONDS, mint_registry_token, verify_registry_token
        from tinyagentos.native_agent_identity import ensure_native_agent_identity, token_path
        registry, grants, data_dir, keypair = await _stores(tmp_path)
        rec = await ensure_native_agent_identity(
            registry=registry, grants=grants, data_dir=data_dir,
            signing_key_pem=keypair[0], user_id="user-1")
        first = token_path(data_dir).read_text().strip()
        # healthy token: left alone (a running agent may hold it)
        await ensure_native_agent_identity(
            registry=registry, grants=grants, data_dir=data_dir,
            signing_key_pem=keypair[0], user_id="user-1")
        assert token_path(data_dir).read_text().strip() == first
        # a token with a day left is renewed, and it is the same identity
        soon = mint_registry_token(rec["canonical_id"], keypair[0], iat=int(time.time()) - AGENT_TOKEN_TTL_SECONDS + 86400)
        token_path(data_dir).write_text(soon)
        await ensure_native_agent_identity(
            registry=registry, grants=grants, data_dir=data_dir,
            signing_key_pem=keypair[0], user_id="user-1")
        new = token_path(data_dir).read_text().strip()
        assert new != soon
        c = verify_registry_token(new, keypair[1])
        assert c["sub"] == rec["canonical_id"] and c["exp"] > time.time() + 86400 * 20
        # the old one is not retired: no cutoff moved
        assert not (await registry.get(rec["canonical_id"])).get("token_min_iat")
