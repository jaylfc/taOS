"""Tests for UserSharesStore — user-to-user resource sharing persistence."""

import pytest
from tinyagentos.user_shares_store import UserSharesStore


def _require_method(obj, name: str, pr: str) -> None:
    """Skip test if *name* does not exist on *obj* (merge-order guard)."""
    if not hasattr(obj, name):
        pytest.skip(f"{name}() not available yet (depends on {pr})")


@pytest.mark.asyncio
class TestUserSharesStore:
    # ── helpers ──────────────────────────────────────────────────────
    async def _store(self, tmp_path):
        s = UserSharesStore(tmp_path / "shares.db")
        await s.init()
        return s

    # ── add_share ────────────────────────────────────────────────────
    async def test_add_share_returns_inserted_row(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.add_share(
                "user-1", "project", "proj-1", "user-2", "read"
            )
            assert row["owner_user_id"] == "user-1"
            assert row["resource_type"] == "project"
            assert row["resource_id"] == "proj-1"
            assert row["shared_with_user_id"] == "user-2"
            assert row["permission"] == "read"
            assert row["tier"] == "once"
            assert row["status"] == "pending"
            assert "granted_at" in row
            assert row["expires_at"] is None
            assert isinstance(row["id"], int)
        finally:
            await store.close()

    async def test_add_share_with_optional_fields(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.add_share(
                "user-1",
                "project",
                "proj-2",
                "user-2",
                "write",
                tier="always",
                expires_at="2030-01-01T00:00:00+00:00",
            )
            assert row["tier"] == "always"
            assert row["expires_at"] == "2030-01-01T00:00:00+00:00"
        finally:
            await store.close()

    async def test_add_share_idempotent_replace(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share(
                "user-1", "project", "proj-3", "user-2", "read", tier="once"
            )
            second = await store.add_share(
                "user-1", "project", "proj-3", "user-2", "read", tier="always"
            )
            assert second["tier"] == "always"
            shares = await store.list_shares("user-1")
            assert len(shares) == 1
            assert shares[0]["tier"] == "always"
        finally:
            await store.close()

    async def test_add_share_uninitialised_raises_runtime_error(self, tmp_path):
        store = UserSharesStore(tmp_path / "shares.db")
        try:
            with pytest.raises(RuntimeError, match="not initialised"):
                await store.add_share(
                    "user-1", "project", "proj-1", "user-2", "read"
                )
        finally:
            await store.close()

    # ── list_shares ──────────────────────────────────────────────────
    async def test_list_shares_returns_all_for_owner(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share("user-a", "project", "p1", "user-b", "read")
            await store.add_share("user-a", "project", "p2", "user-b", "read")
            shares = await store.list_shares("user-a")
            assert len(shares) == 2
            assert {s["resource_id"] for s in shares} == {"p1", "p2"}
        finally:
            await store.close()

    async def test_list_shares_empty_for_unknown_owner(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            shares = await store.list_shares("nonexistent")
            assert shares == []
        finally:
            await store.close()

    async def test_list_shares_scoped_by_owner(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.add_share("user-1", "project", "p1", "user-x", "read")
            await store.add_share("user-2", "project", "p2", "user-x", "read")
            g1 = await store.list_shares("user-1")
            g2 = await store.list_shares("user-2")
            assert len(g1) == 1
            assert len(g2) == 1
            assert g1[0]["owner_user_id"] == "user-1"
            assert g2[0]["owner_user_id"] == "user-2"
        finally:
            await store.close()

    async def test_list_shares_received(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            _require_method(store, "list_shares_received", "#1897")
            await store.add_share("user-a", "project", "p1", "user-b", "read")
            await store.add_share("user-c", "project", "p2", "user-b", "read")
            await store.add_share("user-b", "project", "p3", "user-c", "read")
            received = await store.list_shares_received("user-b")
            assert len(received) == 2
            assert {s["resource_id"] for s in received} == {"p1", "p2"}
        finally:
            await store.close()

    # ── revoke_share ─────────────────────────────────────────────────
    async def test_revoke_share_removes_it(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.add_share(
                "user-1", "project", "p1", "user-2", "read"
            )
            await store.revoke_share(row["id"])
            shares = await store.list_shares("user-1")
            assert len(shares) == 0
        finally:
            await store.close()

    async def test_revoke_nonexistent_share_does_not_crash(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            # Must not raise.
            await store.revoke_share(99999)
        finally:
            await store.close()

    # ── user_can_access ──────────────────────────────────────────────
    async def test_user_can_access_true_for_active_share(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            _require_method(store, "accept_share", "#1897")
            row = await store.add_share(
                "owner", "project", "proj-access", "target", "read"
            )
            # Accept the share so status='accepted' (required by user_can_access).
            await store.accept_share(row["id"])
            can = await store.user_can_access("project", "proj-access", "target")
            assert can is True
        finally:
            await store.close()

    async def test_user_can_access_false_for_expired_share(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            _require_method(store, "accept_share", "#1897")
            row = await store.add_share(
                "owner",
                "project",
                "proj-expired",
                "target",
                "read",
                expires_at="2000-01-01T00:00:00+00:00",
            )
            # Accept so status is 'accepted', but expires_at is in the past.
            await store.accept_share(row["id"])
            can = await store.user_can_access("project", "proj-expired", "target")
            assert can is False
        finally:
            await store.close()

    async def test_user_can_access_false_for_no_share(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            can = await store.user_can_access("project", "no-such-resource", "nobody")
            assert can is False
        finally:
            await store.close()
