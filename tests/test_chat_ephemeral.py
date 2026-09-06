import pytest
import time
from tinyagentos.chat.message_store import ChatMessageStore


@pytest.mark.asyncio
async def test_send_message_with_expires_at(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    exp = time.time() + 3600
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="ephemeral", expires_at=exp,
    )
    got = await store.get_message(msg["id"])
    assert got["expires_at"] is not None
    assert abs(got["expires_at"] - exp) < 1


@pytest.mark.asyncio
async def test_send_message_without_expires_at_defaults_null(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="not ephemeral",
    )
    got = await store.get_message(msg["id"])
    assert got["expires_at"] is None


@pytest.mark.asyncio
async def test_sweep_expired_soft_deletes(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    past = time.time() - 10
    future = time.time() + 3600
    expired_msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="gone", expires_at=past,
    )
    live_msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="live", expires_at=future,
    )
    swept = await store.sweep_expired()
    assert len(swept) == 1
    assert swept[0][0] == expired_msg["id"]
    got = await store.get_message(expired_msg["id"])
    assert got["deleted_at"] is not None
    got_live = await store.get_message(live_msg["id"])
    assert got_live["deleted_at"] is None
