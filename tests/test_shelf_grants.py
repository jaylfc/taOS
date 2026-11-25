"""Shelf grants: the memory:shelf / memory:read records taOSmd reads from the
registry grants feed, carrying their own ``shelf`` field. They are kept apart
from ordinary grants so no project or bus gate can ever count one."""
from __future__ import annotations

import re

import pytest

from tinyagentos.agent_grants_store import AgentGrantsStore, SHELF_SCOPES

_SHELF_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


@pytest.mark.asyncio
class TestShelfGrantStore:
    async def _store(self, tmp_path):
        s = AgentGrantsStore(tmp_path / "grants.db")
        await s.init()
        return s

    async def test_shelf_grant_carries_shelf_field(self, tmp_path):
        s = await self._store(tmp_path)
        try:
            row = await s.add_shelf_grant("agent-1", "memory:shelf", "my-agent")
            assert row["canonical_id"] == "agent-1"
            assert row["scope"] == "memory:shelf"
            assert row["shelf"] == "my-agent"
            assert row["project_id"] is None
            assert row["expires_at"] is None
        finally:
            await s.close()

    async def test_bad_shelf_or_scope_refused(self, tmp_path):
        s = await self._store(tmp_path)
        try:
            for bad in ("", "Upper", "-x", "a b", "x" * 64, None, "../x"):
                with pytest.raises(ValueError):
                    await s.add_shelf_grant("a", "memory:shelf", bad)
            with pytest.raises(ValueError):
                await s.add_shelf_grant("a", "a2a_send", "ok")
        finally:
            await s.close()

    async def test_shelf_grants_never_appear_as_ordinary_grants(self, tmp_path):
        s = await self._store(tmp_path)
        try:
            await s.add_shelf_grant("agent-1", "memory:shelf", "my-agent")
            assert await s.list_grants("agent-1") == []
            assert await s.list_active_grants() == []
            assert [g["shelf"] for g in await s.list_shelf_grants("agent-1")] == ["my-agent"]
        finally:
            await s.close()

    async def test_one_holder_many_shelves_and_revoke_is_exact(self, tmp_path):
        s = await self._store(tmp_path)
        try:
            await s.add_shelf_grant("p", "memory:shelf", "a")
            await s.add_shelf_grant("p", "memory:shelf", "b")
            await s.add_shelf_grant("p", "memory:read", "b")
            assert await s.revoke_shelf_grant("p", "memory:read", "b") is True
            assert await s.revoke_shelf_grant("p", "memory:read", "b") is False
            left = {(g["scope"], g["shelf"]) for g in await s.list_shelf_grants("p")}
            assert left == {("memory:shelf", "a"), ("memory:shelf", "b")}
        finally:
            await s.close()

    async def test_expired_shelf_grant_not_active(self, tmp_path):
        s = await self._store(tmp_path)
        try:
            await s.add_shelf_grant("p", "memory:shelf", "a", expires_at="2000-01-01T00:00:00+00:00")
            assert await s.list_active_shelf_grants() == []
        finally:
            await s.close()

    async def test_upgrade_from_old_db_without_shelf_table(self, tmp_path):
        import aiosqlite
        db = tmp_path / "old.db"
        async with aiosqlite.connect(db) as c:
            await c.execute(
                "CREATE TABLE agent_grants (id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_id TEXT NOT NULL,"
                " scope TEXT NOT NULL, tier TEXT NOT NULL DEFAULT 'once', project_id TEXT, granted_at TEXT NOT NULL,"
                " expires_at TEXT, UNIQUE (canonical_id, scope, project_id))")
            await c.execute("INSERT INTO agent_grants (canonical_id,scope,granted_at) VALUES ('x','a2a_send','t')")
            await c.commit()
        s = AgentGrantsStore(db)
        await s.init()
        try:
            assert [g["scope"] for g in await s.list_active_grants()] == ["a2a_send"]
            await s.add_shelf_grant("x", "memory:shelf", "x-shelf")
            assert len(await s.list_active_shelf_grants()) == 1
        finally:
            await s.close()

    def test_scopes_constant(self):
        assert set(SHELF_SCOPES) == {"memory:shelf", "memory:read"}


# ---------------------------------------------------------------------------
# The grants feed carries shelf records next to ordinary ones
# ---------------------------------------------------------------------------
from tests.test_agent_registry import feeds_client  # noqa: E402,F401


@pytest.mark.asyncio
class TestFeedCarriesShelfGrants:
    async def test_feed_lists_shelf_records_with_shelf_field(self, feeds_client):
        app = feeds_client._app
        await app.state.agent_grants.add_grant("agent-1", "a2a_send")
        await app.state.agent_grants.add_shelf_grant("agent-1", "memory:shelf", "agent-one")
        resp = await feeds_client.get("/api/agents/registry/grants")
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        shelf = [g for g in grants if g["scope"] == "memory:shelf"]
        assert len(shelf) == 1
        assert shelf[0]["canonical_id"] == "agent-1"
        assert shelf[0]["shelf"] == "agent-one"
        assert shelf[0]["project_id"] is None and shelf[0]["expires_at"] is None
        assert _SHELF_RE.fullmatch(shelf[0]["shelf"])
        # ordinary records keep their old shape and never carry a shelf
        ordinary = [g for g in grants if g["scope"] == "a2a_send"]
        assert ordinary and "shelf" not in ordinary[0]

    async def test_feed_canonical_id_filter_includes_that_agents_shelf(self, feeds_client):
        app = feeds_client._app
        await app.state.agent_grants.add_shelf_grant("a", "memory:shelf", "sa")
        await app.state.agent_grants.add_shelf_grant("b", "memory:shelf", "sb")
        resp = await feeds_client.get("/api/agents/registry/grants?canonical_id=a")
        assert [g["shelf"] for g in resp.json()["grants"]] == ["sa"]

    async def test_expired_shelf_grant_not_in_feed(self, feeds_client):
        app = feeds_client._app
        await app.state.agent_grants.add_shelf_grant(
            "a", "memory:shelf", "sa", expires_at="2000-01-01T00:00:00+00:00")
        resp = await feeds_client.get("/api/agents/registry/grants")
        assert resp.json()["grants"] == []
