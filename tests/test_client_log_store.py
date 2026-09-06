import pytest
import pytest_asyncio

from tinyagentos.client_log_store import ClientLogStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ClientLogStore(tmp_path / "client_logs.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_and_list(store):
    await store.create(
        user_id="u1", level="error", message="boom",
        source="MessagesApp", url="/desktop", stack="at foo",
    )
    items = await store.list_recent()
    assert len(items) == 1
    assert items[0]["level"] == "error"
    assert items[0]["message"] == "boom"
    assert items[0]["source"] == "MessagesApp"
    assert items[0]["url"] == "/desktop"


@pytest.mark.asyncio
async def test_list_filters_by_level(store):
    await store.create(user_id="u1", level="error", message="e")
    await store.create(user_id="u1", level="info", message="i")
    errs = await store.list_recent(level="error")
    assert len(errs) == 1
    assert errs[0]["level"] == "error"


@pytest.mark.asyncio
async def test_long_message_and_stack_are_truncated(store):
    await store.create(
        user_id="u1", level="error",
        message="x" * 5000, stack="y" * 20000,
    )
    item = (await store.list_recent())[0]
    assert len(item["message"]) == 4000
    assert len(item["stack"]) == 16000


@pytest.mark.asyncio
async def test_ring_buffer_caps_total_rows(store, monkeypatch):
    monkeypatch.setattr("tinyagentos.client_log_store.MAX_ROWS", 3)
    for i in range(6):
        await store.create(user_id="u1", level="debug", message=f"m{i}")
    items = await store.list_recent(limit=100)
    assert len(items) == 3
    # The newest entry is retained; the oldest are pruned.
    msgs = [i["message"] for i in items]
    assert "m5" in msgs
    assert "m0" not in msgs


@pytest.mark.asyncio
async def test_prune_and_order_use_rowid_when_timestamps_tie(store, monkeypatch):
    monkeypatch.setattr("tinyagentos.client_log_store.MAX_ROWS", 3)
    for i in range(5):
        await store.create(user_id="u1", level="debug", message=f"m{i}")
    # Force m0..m4 to share one created_at so the timestamp cannot disambiguate
    # them; only the rowid tie-breaker keeps the prune + ordering deterministic.
    await store._db.execute(
        "UPDATE client_logs SET created_at = '2026-01-01T00:00:00+00:00'")
    await store._db.commit()
    # This 6th insert triggers the prune; a rowid-based ring buffer must keep the
    # last-inserted three (m3, m4, m5), not an arbitrary trio of the tied rows.
    await store.create(user_id="u1", level="debug", message="m5")
    msgs = [i["message"] for i in await store.list_recent(limit=100)]
    assert len(msgs) == 3
    assert msgs == ["m5", "m4", "m3"]


@pytest.mark.asyncio
async def test_prune_keeps_full_buffer_when_rowids_have_gaps(store, monkeypatch):
    # Regression: an out-of-band delete (per-user eviction, rolled-back insert)
    # leaves a rowid gap. A value-threshold prune (rowid <= MAX(rowid) - MAX_ROWS)
    # would then retain FEWER than MAX_ROWS rows; the rank-based prune must keep
    # exactly the newest MAX_ROWS regardless of gaps.
    monkeypatch.setattr("tinyagentos.client_log_store.MAX_ROWS", 3)
    for i in range(5):
        await store.create(user_id="u1", level="debug", message=f"m{i}")
    # Steady state holds 3 rows; punch a gap in the retained window by deleting the
    # middle retained rowid directly (simulating an out-of-band delete).
    async with store._db.execute(
        "SELECT rowid FROM client_logs ORDER BY rowid"
    ) as cur:
        rowids = [r[0] for r in await cur.fetchall()]
    assert len(rowids) == 3
    await store._db.execute(
        "DELETE FROM client_logs WHERE rowid = ?", (rowids[1],))
    await store._db.commit()
    # One more insert triggers the prune; the buffer must refill to MAX_ROWS, not
    # collapse to 2 because of the gap.
    await store.create(user_id="u1", level="debug", message="m5")
    items = await store.list_recent(limit=100)
    assert len(items) == 3
