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
    plaintext_a, row_a = await store.issue(agent_id="agent-1", user_id="u", scope=["*"])
    plaintext_b, row_b = await store.issue(agent_id="agent-1", user_id="u", scope=["*"])
    assert row_b["revoked_at"] is None
    looked_up_old = await store.lookup_by_plaintext(plaintext_a)
    assert looked_up_old is None
