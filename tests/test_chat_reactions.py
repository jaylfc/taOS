import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.chat.reactions import maybe_trigger_semantic, WantsReplyRegistry


class _FakeManifest:
    def __init__(self, context_window):
        self.type = "model"
        self.context_window = context_window


class _FakeRegistry:
    def __init__(self, by_id):
        self._by_id = by_id

    def get(self, model_id):
        return self._by_id.get(model_id)

    def list_available(self, type_filter=None):
        return list(self._by_id.values())


@pytest.mark.asyncio
async def test_regenerate_triggered_for_thumbs_down_on_agent_reply():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value={
        "id": "user-msg-1", "channel_id": "c1", "content": "what's 2+2?",
    })
    msg_store.get_messages = AsyncMock(return_value=[
        {"author_id": "user", "author_type": "user", "content": "what's 2+2?"},
        {"author_id": "tom", "author_type": "agent", "content": "bad answer"},
    ])
    state = MagicMock(bridge_sessions=bridge, chat_messages=msg_store)
    state.wants_reply = WantsReplyRegistry()
    message = {
        "id": "m1", "channel_id": "c1", "author_id": "tom",
        "author_type": "agent", "content": "bad answer",
        "metadata": {"trace_id": "user-msg-1"},
    }
    channel = {"id": "c1", "members": ["user", "tom"], "type": "dm",
               "settings": {}}
    await maybe_trigger_semantic(
        emoji="👎", message=message, reactor_id="user", reactor_type="user",
        channel=channel, state=state,
    )
    bridge.enqueue_user_message.assert_awaited_once()
    call = bridge.enqueue_user_message.await_args
    assert call.args[0] == "tom"
    assert call.args[1]["force_respond"] is True
    assert call.args[1].get("regenerate") is True
    assert call.args[1]["text"] == "what's 2+2?"
    assert call.args[1]["trace_id"] == "user-msg-1"
    assert len(call.args[1]["context"]) > 0


@pytest.mark.asyncio
async def test_regenerate_uses_agent_model_history_budget():
    """Small-context agent model floors history max_tokens at 512 (#1740)."""
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value={
        "id": "user-msg-1", "channel_id": "c1", "content": "hi",
    })
    msg_store.get_messages = AsyncMock(return_value=[
        {"author_id": "user", "author_type": "user", "content": "hi"},
        {"author_id": "tom", "author_type": "agent", "content": "nope"},
    ])
    config = MagicMock()
    config.agents = [{"name": "tom", "model": "tiny-rkllm"}]
    registry = _FakeRegistry({"tiny-rkllm": _FakeManifest(4096)})
    state = MagicMock(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        config=config,
        registry=registry,
    )
    state.wants_reply = WantsReplyRegistry()
    message = {
        "id": "m1", "channel_id": "c1", "author_id": "tom",
        "author_type": "agent", "content": "nope",
        "metadata": {"trace_id": "user-msg-1"},
    }
    channel = {"id": "c1", "members": ["user", "tom"], "type": "dm", "settings": {}}

    with patch(
        "tinyagentos.chat.context_window.build_context_window",
        return_value=[{"author_id": "user", "author_type": "user", "content": "hi"}],
    ) as build:
        await maybe_trigger_semantic(
            emoji="👎", message=message, reactor_id="user", reactor_type="user",
            channel=channel, state=state,
        )
    build.assert_called_once()
    assert build.call_args.kwargs["max_tokens"] == 512


@pytest.mark.asyncio
async def test_thumbs_down_from_agent_is_noop():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    state = MagicMock(bridge_sessions=bridge)
    state.wants_reply = WantsReplyRegistry()
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "content": "x"}
    channel = {"id": "c1", "members": ["user", "tom", "don"], "type": "group", "settings": {}}
    await maybe_trigger_semantic(
        emoji="👎", message=message, reactor_id="don", reactor_type="agent",
        channel=channel, state=state,
    )
    bridge.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_hand_raise_sets_wants_reply():
    state = MagicMock()
    state.wants_reply = WantsReplyRegistry()
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "content": "x"}
    channel = {"id": "c1", "members": ["user", "tom", "don"], "type": "group", "settings": {}}
    await maybe_trigger_semantic(
        emoji="🙋", message=message, reactor_id="don", reactor_type="agent",
        channel=channel, state=state,
    )
    assert "don" in state.wants_reply.list("c1")


def test_wants_reply_expires_after_ttl(monkeypatch):
    r = WantsReplyRegistry(ttl_seconds=60)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: t[0])
    r.add("c1", "don")
    assert "don" in r.list("c1")
    t[0] = 1061.0
    assert "don" not in r.list("c1")
