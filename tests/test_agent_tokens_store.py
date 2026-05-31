"""Tests for AgentTokensStore — multi-worker-safe token issuance."""
import asyncio

import aiosqlite
import pytest

from tinyagentos.agent_tokens_store import AgentTokenExistsError, AgentTokensStore


def _issue(store, *args):
    """Small helper so tests can call issue without 'await' in the trace."""
    return store.issue(*args)


@pytest.mark.asyncio
class TestAgentTokensStore:
    # ── helpers ──────────────────────────────────────────────────────
    async def _make_store(self, db_path):
        store = AgentTokensStore(db_path)
        await store.init()
        return store

    # ── normal operation ─────────────────────────────────────────────
    async def test_issue_returns_row_dict(self, tmp_path):
        """issue() returns a dict with all expected fields."""
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            result = await store.issue("agent-a", "tok-abc123")
            assert result["agent_name"] == "agent-a"
            assert result["token"] == "tok-abc123"
            assert result["revoked_at"] is None
            assert "created_at" in result
            assert "id" in result
            assert isinstance(result["id"], int)
        finally:
            await store.close()

    # ── constraint violation → clean error ───────────────────────────
    async def test_duplicate_raises_agent_token_exists_error(self, tmp_path):
        """Second issue() for the same agent raises AgentTokenExistsError."""
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            await store.issue("agent-a", "token-first")

            with pytest.raises(AgentTokenExistsError) as exc:
                await store.issue("agent-a", "token-second")
            assert exc.value.agent_name == "agent-a"
            assert "already has an active token" in str(exc.value)
        finally:
            await store.close()

    # ── re-issue after revoke ────────────────────────────────────────
    async def test_reissue_after_revoke_succeeds(self, tmp_path):
        """After revoking, a new token can be issued for the same agent."""
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            first = await store.issue("agent-a", "token-first")
            revoked = await store.revoke("agent-a")
            assert revoked is not None
            assert revoked["revoked_at"] is not None

            second = await store.issue("agent-a", "token-second")
            assert second["token"] == "token-second"
            assert second["id"] != first["id"]
        finally:
            await store.close()

    # ── BEGIN IMMEDIATE prevents concurrent INSERT ───────────────────
    async def test_concurrent_duplicate_prevented_across_stores(self, tmp_path):
        """Two AgentTokensStore instances on the same DB file:
        the second issue() raises AgentTokenExistsError because
        BEGIN IMMEDIATE makes the first INSERT visible immediately."""
        db = tmp_path / "shared.db"
        store1 = await self._make_store(db)
        store2 = await self._make_store(db)
        try:
            await store1.issue("agent-x", "tok-st1")

            with pytest.raises(AgentTokenExistsError) as exc:
                await store2.issue("agent-x", "tok-st2")
            assert exc.value.agent_name == "agent-x"
        finally:
            await store1.close()
            await store2.close()

    async def test_begin_immediate_blocks_concurrent_writer(self, tmp_path):
        """A raw connection holding a write transaction via
        BEGIN IMMEDIATE blocks the store's issue() call until
        the raw connection releases the lock, at which point
        the unique constraint fires."""
        db = tmp_path / "locked.db"
        store = await self._make_store(db)

        raw = await aiosqlite.connect(str(db))
        try:
            # Grab the reserved lock before the store does
            await raw.execute("BEGIN IMMEDIATE")
            await raw.execute(
                "INSERT INTO agent_tokens (agent_name, token, created_at) "
                "VALUES ('agent-y', 'tok-raw', '2026-01-01T00:00:00')",
            )
            # Do NOT commit — hold the lock

            # The store's issue() uses BEGIN IMMEDIATE and will block.
            # aiosqlite has a busy timeout; the lock will be held until
            # we release it.  Schedule the store's issue as a task,
            # verify it's blocked, then release and see the result.
            import time

            async def delayed_issue():
                return await store.issue("agent-y", "tok-store")

            task = asyncio.create_task(delayed_issue())

            # Give the task a moment to hit the lock
            await asyncio.sleep(0.1)
            assert not task.done(), "issue() should be blocked by BEGIN IMMEDIATE"

            # Release the raw lock
            await raw.commit()
            # Now the store's BEGIN IMMEDIATE can proceed, but the INSERT
            # will hit the unique constraint because 'agent-y' already
            # has an active token from the raw connection.
            with pytest.raises(AgentTokenExistsError):
                await task
        finally:
            await raw.close()
            await store.close()

    # ── get_active ───────────────────────────────────────────────────
    async def test_get_active_returns_none_for_unknown(self, tmp_path):
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            assert await store.get_active("no-such-agent") is None
        finally:
            await store.close()

    async def test_get_active_after_issue(self, tmp_path):
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            await store.issue("agent-b", "tok-bbb")
            row = await store.get_active("agent-b")
            assert row is not None
            assert row["token"] == "tok-bbb"
        finally:
            await store.close()

    async def test_get_active_after_revoke_returns_none(self, tmp_path):
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            await store.issue("agent-c", "tok-ccc")
            await store.revoke("agent-c")
            assert await store.get_active("agent-c") is None
        finally:
            await store.close()

    # ── revoke ───────────────────────────────────────────────────────
    async def test_revoke_returns_row_with_revoked_at(self, tmp_path):
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            await store.issue("agent-d", "tok-d")
            result = await store.revoke("agent-d")
            assert result is not None
            assert result["token"] == "tok-d"
            assert result["revoked_at"] is not None
        finally:
            await store.close()

    async def test_revoke_nonexistent_returns_none(self, tmp_path):
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            assert await store.revoke("no-such-agent") is None
        finally:
            await store.close()

    async def test_different_agents_do_not_interfere(self, tmp_path):
        store = await self._make_store(tmp_path / "tokens.db")
        try:
            a = await store.issue("agent-a", "tok-a")
            b = await store.issue("agent-b", "tok-b")
            assert a["agent_name"] == "agent-a"
            assert b["agent_name"] == "agent-b"

            # Revoking one does not affect the other
            await store.revoke("agent-a")
            assert await store.get_active("agent-a") is None
            assert await store.get_active("agent-b") is not None
        finally:
            await store.close()
