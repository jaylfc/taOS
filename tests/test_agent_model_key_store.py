"""Tests for AgentModelKeyStore — Agent-as-a-Model consent keys (decision 19)."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from tinyagentos.agent_model_key_store import AgentModelKeyStore


@pytest.mark.asyncio
class TestAgentModelKeyStore:
    async def _store(self, tmp_path):
        s = AgentModelKeyStore(tmp_path / "amk.db")
        await s.init()
        return s

    async def test_mint_returns_token_and_record(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            token, rec = await store.mint("u1", ["agent-a"], ["memory_read"])
            assert token.startswith("sk-taosagent-")
            assert rec["issuing_user"] == "u1"
            assert rec["agent_ids"] == ["agent-a"]
            assert rec["scopes"] == ["memory_read"]
            assert rec["revoked"] is False
            # The plaintext token and its hash are never surfaced in the record.
            assert "key_hash" not in rec
            assert token not in str(rec)
        finally:
            await store.close()

    async def test_mint_stores_only_the_hash(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            token, _ = await store.mint("u1", ["agent-a"], [])
            row = await (
                await store._db.execute("SELECT key_hash FROM agent_model_keys")
            ).fetchone()
            assert row["key_hash"] == hashlib.sha256(token.encode()).hexdigest()
            assert row["key_hash"] != token  # plaintext is not stored
        finally:
            await store.close()

    async def test_mint_requires_an_agent(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            with pytest.raises(ValueError, match="at least one agent"):
                await store.mint("u1", [], ["memory_read"])
        finally:
            await store.close()

    async def test_resolve_valid_token(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            token, _ = await store.mint("u1", ["agent-a", "agent-b"], ["memory_read"])
            rec = await store.resolve(token)
            assert rec is not None
            assert rec["issuing_user"] == "u1"
            assert rec["agent_ids"] == ["agent-a", "agent-b"]
        finally:
            await store.close()

    async def test_resolve_unknown_token_is_none(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            assert await store.resolve("sk-taosagent-nope") is None
        finally:
            await store.close()

    async def test_revoke_kills_the_key(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            token, rec = await store.mint("u1", ["agent-a"], [])
            assert await store.revoke(rec["id"]) is True
            assert await store.resolve(token) is None
            # Revoking again is a no-op.
            assert await store.revoke(rec["id"]) is False
        finally:
            await store.close()

    async def test_expired_token_does_not_resolve(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            token, _ = await store.mint("u1", ["agent-a"], [], expires_at=past)
            assert await store.resolve(token) is None
        finally:
            await store.close()

    async def test_expiry_normalizes_z_suffix(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            future_z = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            token, _ = await store.mint("u1", ["agent-a"], [], expires_at=future_z)
            assert await store.resolve(token) is not None  # future -> still valid
            past_z = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            t2, _ = await store.mint("u1", ["agent-a"], [], expires_at=past_z)
            assert await store.resolve(t2) is None  # past -> expired
        finally:
            await store.close()

    async def test_expiry_honours_real_utc_not_lexical_offset(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            # 12:00+05:00 is 07:00 UTC. If it is already past in UTC the key must
            # NOT resolve, even though "12:00" lexically looks later than now.
            past_utc_via_offset = (
                (datetime.now(timezone.utc) - timedelta(hours=2))
                .astimezone(timezone(timedelta(hours=5)))
                .isoformat()
            )
            token, _ = await store.mint("u1", ["agent-a"], [], expires_at=past_utc_via_offset)
            assert await store.resolve(token) is None
        finally:
            await store.close()

    async def test_naive_expiry_is_rejected(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            with pytest.raises(ValueError, match="timezone-aware"):
                await store.mint("u1", ["agent-a"], [], expires_at="2026-06-10T12:00:00")
        finally:
            await store.close()

    async def test_list_for_user_excludes_secrets(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.mint("u1", ["agent-a"], ["memory_read"], rate_cap=60)
            await store.mint("u2", ["agent-z"], [])
            keys = await store.list_for_user("u1")
            assert len(keys) == 1
            assert keys[0]["issuing_user"] == "u1"
            assert keys[0]["rate_cap"] == 60
            assert "key_hash" not in keys[0]
        finally:
            await store.close()

    @pytest.mark.parametrize(
        "agent_ids",
        [
            ["../../x"],
            ["a/b"],
            ["a\\b"],
            ["/etc/passwd"],
            ["valid", "../../escape"],
            ["a b"],
            [""],
        ],
    )
    async def test_mint_rejects_path_traversal_agent_ids(self, tmp_path, agent_ids):
        """Agent ids become filesystem path components — reject traversal.

        Note: dots are allowed (safe slug set); ``...`` or ``../../x`` without
        slashes are harmless as a single path component — the slugify layer
        in taos_agent_runtime replaces the unsafe separators.
        """
        store = await self._store(tmp_path)
        try:
            with pytest.raises(ValueError):
                await store.mint("u1", agent_ids, [])
        finally:
            await store.close()
