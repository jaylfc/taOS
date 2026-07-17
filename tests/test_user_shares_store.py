import pytest

from tinyagentos.user_shares_store import UserSharesStore


@pytest.mark.asyncio
class TestUserSharesStore:
    async def _store(self, tmp_path):
        s = UserSharesStore(tmp_path / "shares.db")
        await s.init()
        return s

    # ── add_share ────────────────────────────────────────────────────
    async def test_add_share_returns_inserted_row(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.add_share("alice", "taosmd", "mem-1", "bob", "read")
            assert row["owner_user_id"] == "alice"
            assert row["resource_type"] == "taosmd"
            assert row["resource_id"] == "mem-1"
            assert row["shared_with_user_id"] == "bob"
            assert row["permission"] == "read"
            assert row["tier"] == "once"
            assert row["expires_at"] is None
            assert "granted_at" in row
            assert isinstance(row["id"], int)
        finally:
            await store.close()

    async def test_add_share_with_optional_fields(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.add_share(
                "alice", "taosmd", "mem-1", "bob", "read",
                tier="always", expires_at="2999-01-01T00:00:00+00:00",
            )
            assert row["tier"] == "always"
            assert row["expires_at"] == "2999-01-01T00:00:00+00:00"
        finally:
            await store.close()

    async def test_add_share_idempotent_replace(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share("alice", "taosmd", "mem-1", "bob", "read")
            await store.add_share("alice", "taosmd", "mem-1", "bob", "read", tier="always")
            rows = await store.list_shares("alice")
            assert len(rows) == 1
            assert rows[0]["tier"] == "always"
        finally:
            await store.close()

    async def test_add_share_uninitialised_raises(self, tmp_path):
        store = UserSharesStore(tmp_path / "shares.db")
        with pytest.raises(RuntimeError):
            await store.add_share("alice", "taosmd", "mem-1", "bob", "read")

    # ── list ─────────────────────────────────────────────────────────
    async def test_list_shares_out_and_in(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share("alice", "taosmd", "mem-1", "bob", "read")
            await store.add_share("alice", "taosmd", "mem-2", "carol", "read")
            assert len(await store.list_shares("alice")) == 2
            received = await store.list_shares_received("bob")
            assert len(received) == 1
            assert received[0]["resource_id"] == "mem-1"
            assert await store.list_shares_received("dave") == []
        finally:
            await store.close()

    async def test_list_active_shares_excludes_expired(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share("alice", "taosmd", "live", "bob", "read")
            await store.add_share(
                "alice", "taosmd", "dead", "bob", "read",
                expires_at="2000-01-01T00:00:00+00:00",
            )
            active = await store.list_active_shares()
            ids = {s["resource_id"] for s in active}
            assert "live" in ids and "dead" not in ids
        finally:
            await store.close()

    # ── revoke ───────────────────────────────────────────────────────
    async def test_revoke_share_removes_it(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.add_share("alice", "taosmd", "mem-1", "bob", "read")
            await store.revoke_share(row["id"])
            assert await store.list_shares("alice") == []
        finally:
            await store.close()

    # ── user_can_access ──────────────────────────────────────────────
    async def test_user_can_access_true_for_target_false_for_other(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share("alice", "taosmd", "mem-1", "bob", "read")
            assert await store.user_can_access("taosmd", "mem-1", "bob") is True
            assert await store.user_can_access("taosmd", "mem-1", "carol") is False
            assert await store.user_can_access("taosmd", "other", "bob") is False
        finally:
            await store.close()

    async def test_user_can_access_false_when_expired(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share(
                "alice", "taosmd", "mem-1", "bob", "read",
                expires_at="2000-01-01T00:00:00+00:00",
            )
            assert await store.user_can_access("taosmd", "mem-1", "bob") is False
        finally:
            await store.close()
