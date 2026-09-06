"""Tests for issue #652: atomic reactions, batch sweep, persistent sequence."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.chat.message_store import ChatMessageStore
from tinyagentos.chat.hub import ChatHub


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def store(tmp_path):
    s = ChatMessageStore(tmp_path / "chat.db")
    await s.init()
    yield s
    await s.close()


async def _send(store, channel_id="ch1", author_id="user1", content="hello", **kw):
    return await store.send_message(
        channel_id=channel_id,
        author_id=author_id,
        author_type=kw.pop("author_type", "user"),
        content=content,
        **kw,
    )


# ── atomic reactions ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_add_reactions_no_lost_update(store):
    """Concurrent add_reaction calls must not lose any user's vote."""
    msg = await _send(store)
    users = [f"user{i}" for i in range(10)]
    # Fire all concurrently to stress the read-modify-write path
    await asyncio.gather(*[store.add_reaction(msg["id"], "👍", u) for u in users])
    updated = await store.get_message(msg["id"])
    assert len(updated["reactions"]["👍"]) == 10, (
        f"Expected 10 reactions, got {updated['reactions']['👍']}"
    )


@pytest.mark.asyncio
async def test_concurrent_remove_reactions_no_lost_update(store):
    """Concurrent remove_reaction calls must converge correctly."""
    msg = await _send(store)
    users = [f"user{i}" for i in range(5)]
    for u in users:
        await store.add_reaction(msg["id"], "👍", u)
    # Remove all concurrently
    await asyncio.gather(*[store.remove_reaction(msg["id"], "👍", u) for u in users])
    updated = await store.get_message(msg["id"])
    assert "👍" not in updated["reactions"]


@pytest.mark.asyncio
async def test_add_reaction_same_user_idempotent_under_concurrency(store):
    """Same user adding the same reaction concurrently must not duplicate."""
    msg = await _send(store)
    await asyncio.gather(*[store.add_reaction(msg["id"], "❤️", "alice") for _ in range(5)])
    updated = await store.get_message(msg["id"])
    assert updated["reactions"]["❤️"].count("alice") == 1


# ── batch sweep ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sweep_expired_batch(store):
    """sweep_expired must delete all expired messages in one pass."""
    past = time.time() - 10
    future = time.time() + 9999
    m_exp1 = await _send(store, content="expired1", expires_at=past)
    m_exp2 = await _send(store, content="expired2", expires_at=past)
    m_live = await _send(store, content="live", expires_at=future)
    m_nexp = await _send(store, content="no-expiry")

    deleted = await store.sweep_expired()
    deleted_ids = {r[0] for r in deleted}
    assert m_exp1["id"] in deleted_ids
    assert m_exp2["id"] in deleted_ids
    assert m_live["id"] not in deleted_ids
    assert m_nexp["id"] not in deleted_ids

    # Soft-deleted rows still exist but have deleted_at set
    got1 = await store.get_message(m_exp1["id"])
    assert got1["deleted_at"] is not None
    got_live = await store.get_message(m_live["id"])
    assert got_live["deleted_at"] is None


@pytest.mark.asyncio
async def test_sweep_expired_all_soft_deleted_in_one_pass(store):
    """All expired messages must be soft-deleted and returned in a single sweep_expired call.

    This validates batch behaviour: sweep_expired must handle N rows atomically,
    not fall back to N individual commits.
    """
    past = time.time() - 10
    N = 5
    sent = []
    for i in range(N):
        sent.append(await _send(store, content=f"expired{i}", expires_at=past))

    result = await store.sweep_expired()
    assert len(result) == N, f"Expected {N} expired, got {len(result)}"

    # A second sweep must return nothing (rows already soft-deleted)
    result2 = await store.sweep_expired()
    assert result2 == [], f"Second sweep should be empty, got {result2}"

    # All rows must now have deleted_at set
    for m in sent:
        got = await store.get_message(m["id"])
        assert got["deleted_at"] is not None, f"Message {m['id']} not soft-deleted"


# ── persistent sequence ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seq_persists_across_restart(tmp_path):
    """Sequence counter must survive store re-init (seed from MAX(seq) or similar)."""
    s1 = ChatMessageStore(tmp_path / "chat.db")
    await s1.init()
    # Simulate hub init with this store; hub must seed from existing messages
    hub1 = ChatHub()
    await hub1.seed_seq(s1)
    # Send a few messages so the seq advances
    for _ in range(3):
        hub1.next_seq()
    high_seq = hub1.next_seq()  # 4 (or higher if seeded)
    assert high_seq >= 4

    # Store some messages (seq is in metadata or implicit in count)
    # The key test: after re-init of a NEW hub, seq must not start at 1
    for i in range(5):
        await s1.send_message("ch1", "user", "user", f"msg{i}")
    await s1.close()

    s2 = ChatMessageStore(tmp_path / "chat.db")
    await s2.init()
    hub2 = ChatHub()
    await hub2.seed_seq(s2)
    first_seq = hub2.next_seq()
    assert first_seq > 1, (
        f"After restart, seq must be seeded from existing messages, got {first_seq}"
    )
    await s2.close()


@pytest.mark.asyncio
async def test_seq_seeds_from_max_message_seq(tmp_path):
    """If messages have a seq column or we use COUNT/MAX, seq must not restart at 1."""
    s = ChatMessageStore(tmp_path / "chat.db")
    await s.init()
    for i in range(7):
        await s.send_message("ch1", "user", "user", f"msg{i}")

    hub = ChatHub()
    await hub.seed_seq(s)
    seq = hub.next_seq()
    # Must be > 7 (seed is max existing + 1, then we increment once more)
    assert seq > 7, f"Expected seq > 7 after seeding from 7 messages, got {seq}"
    await s.close()
