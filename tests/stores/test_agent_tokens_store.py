import pytest
import pytest_asyncio
import tempfile
from pathlib import Path

from tinyagentos.stores.agent_tokens_store import AgentTokensStore


@pytest_asyncio.fixture
async def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = AgentTokensStore(Path(tmp) / "tokens.db")
        await s.init()
        yield s
        await s.close()


@pytest.mark.asyncio
async def test_issue_returns_plaintext_token_with_prefix(store):
    plaintext, row = await store.issue(
        agent_id="agent-1",
        user_id="user-1",
        scope=["*"],
    )
    assert plaintext.startswith("taos_agent_")
    assert len(plaintext) > 50
    assert row["agent_id"] == "agent-1"
    assert row["user_id"] == "user-1"
    assert row["scope"] == ["*"]
    assert row["revoked_at"] is None
    assert row["issued_at"] is not None


@pytest.mark.asyncio
async def test_issue_stores_hash_not_plaintext(store):
    plaintext, row = await store.issue(agent_id="a", user_id="u", scope=["*"])
    assert row["token_hash"] != plaintext
    assert len(row["token_hash"]) == 64


@pytest.mark.asyncio
async def test_issue_revokes_previous_token_atomically(store):
    plaintext_a, _row_a = await store.issue(agent_id="agent-1", user_id="u", scope=["*"])
    _plaintext_b, row_b = await store.issue(agent_id="agent-1", user_id="u", scope=["*"])
    assert row_b["revoked_at"] is None
    looked_up_old = await store.lookup_by_plaintext(plaintext_a)
    assert looked_up_old is None


@pytest.mark.asyncio
async def test_revoke_for_agent_marks_revoked(store):
    plaintext, _ = await store.issue(agent_id="a", user_id="u", scope=["*"])
    revoked = await store.revoke_for_agent("a")
    assert revoked == 1
    assert await store.lookup_by_plaintext(plaintext) is None


@pytest.mark.asyncio
async def test_revoke_for_agent_returns_zero_when_no_active_token(store):
    revoked = await store.revoke_for_agent("nonexistent")
    assert revoked == 0


@pytest.mark.asyncio
async def test_has_token_returns_true_when_active(store):
    await store.issue(agent_id="a", user_id="u", scope=["*"])
    assert await store.has_token("a") is True


@pytest.mark.asyncio
async def test_has_token_returns_false_when_revoked(store):
    await store.issue(agent_id="a", user_id="u", scope=["*"])
    await store.revoke_for_agent("a")
    assert await store.has_token("a") is False


@pytest.mark.asyncio
async def test_touch_last_used_updates_timestamp(store):
    plaintext, _ = await store.issue(agent_id="a", user_id="u", scope=["*"])
    await store.touch_last_used(plaintext)
    row = await store.lookup_by_plaintext(plaintext)
    assert row["last_used_at"] is not None


@pytest.mark.asyncio
async def test_get_metadata_returns_non_secret_fields(store):
    await store.issue(agent_id="a", user_id="u", scope=["*"])
    meta = await store.get_metadata("a")
    assert meta is not None
    assert meta["has_token"] is True
    assert meta["issued_at"] is not None
    assert meta["last_used_at"] is None
    assert set(meta.keys()) == {"has_token", "issued_at", "last_used_at"}


@pytest.mark.asyncio
async def test_get_metadata_returns_none_when_no_token(store):
    assert await store.get_metadata("nonexistent") is None


@pytest.mark.asyncio
async def test_get_metadata_returns_none_after_revoke(store):
    await store.issue(agent_id="a", user_id="u", scope=["*"])
    await store.revoke_for_agent("a")
    assert await store.get_metadata("a") is None
