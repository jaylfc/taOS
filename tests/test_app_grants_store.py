"""Tests for AppGrantsStore — per-app capability grants persistence (#56)."""
import pytest

from tinyagentos.app_grants_store import AppGrantsStore


@pytest.mark.asyncio
class TestAppGrantsStore:
    async def _store(self, tmp_path):
        s = AppGrantsStore(tmp_path / "app_grants.db")
        await s.init()
        return s

    async def test_set_decision_returns_inserted_row(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.set_decision("u1", "stream-chat", "app.kv")
            assert row["user_id"] == "u1"
            assert row["app_id"] == "stream-chat"
            assert row["capability"] == "app.kv"
            assert row["decision"] == "granted"
            assert "decided_at" in row
            assert isinstance(row["id"], int)
        finally:
            await store.close()

    async def test_set_decision_rejects_unknown_decision(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            with pytest.raises(ValueError, match="invalid decision"):
                await store.set_decision("u1", "a", "app.kv", decision="maybe")
        finally:
            await store.close()

    async def test_set_decision_idempotent_replace(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.set_decision("u1", "a", "files.read", decision="denied")
            await store.set_decision("u1", "a", "files.read", decision="granted")
            grants = await store.list_grants("u1", "a")
            assert len(grants) == 1
            assert grants[0]["decision"] == "granted"
        finally:
            await store.close()

    async def test_granted_capabilities_excludes_denied(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.set_decision("u1", "a", "app.kv", decision="granted")
            await store.set_decision("u1", "a", "network", decision="denied")
            await store.set_decision("u1", "a", "notifications.post", decision="granted")
            assert await store.granted_capabilities("u1", "a") == {"app.kv", "notifications.post"}
        finally:
            await store.close()

    async def test_revoke_records_denial_not_deletion(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.set_decision("u1", "a", "files.write", decision="granted")
            await store.revoke("u1", "a", "files.write")
            # The capability is no longer granted...
            assert await store.granted_capabilities("u1", "a") == set()
            # ...but the row is kept as a durable denial (append-only #103).
            grants = await store.list_grants("u1", "a")
            assert len(grants) == 1
            assert grants[0]["decision"] == "denied"
        finally:
            await store.close()

    async def test_grants_scoped_by_user(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.set_decision("u1", "a", "app.kv", decision="granted")
            await store.set_decision("u2", "a", "app.kv", decision="denied")
            assert await store.granted_capabilities("u1", "a") == {"app.kv"}
            assert await store.granted_capabilities("u2", "a") == set()
        finally:
            await store.close()

    async def test_set_decision_uninitialised_raises(self, tmp_path):
        store = AppGrantsStore(tmp_path / "app_grants.db")
        with pytest.raises(RuntimeError):
            await store.set_decision("u1", "a", "app.kv")
