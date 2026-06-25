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
