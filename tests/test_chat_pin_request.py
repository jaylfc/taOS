import pytest
from tinyagentos.chat.message_store import ChatMessageStore
from tinyagentos.chat.reactions import maybe_trigger_semantic


class _FakeState:
    def __init__(self, store):
        self.chat_messages = store


@pytest.mark.asyncio
async def test_agent_pin_own_message_sets_flag(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="see this",
    )
    await maybe_trigger_semantic(
        emoji="📌", message=msg,
        reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=_FakeState(store),
    )
    updated = await store.get_message(msg["id"])
    assert updated["metadata"].get("pin_requested") is True


@pytest.mark.asyncio
async def test_agent_pin_other_message_does_not_set_flag(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="don", author_type="agent", content="don's msg",
    )
    await maybe_trigger_semantic(
        emoji="📌", message=msg,
        reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=_FakeState(store),
    )
    updated = await store.get_message(msg["id"])
    assert updated["metadata"].get("pin_requested") is None


@pytest.mark.asyncio
async def test_human_pin_reaction_does_not_set_flag(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent", content="x",
    )
    await maybe_trigger_semantic(
        emoji="📌", message=msg,
        reactor_id="jay", reactor_type="user",
        channel={"id": "c1"}, state=_FakeState(store),
    )
    updated = await store.get_message(msg["id"])
    assert updated["metadata"].get("pin_requested") is None
