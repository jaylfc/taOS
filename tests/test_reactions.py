"""Focused unit tests for ``tinyagentos.chat.reactions``.

The module exposes three testable units:

* ``_now`` -- trivial; exercised through the registry TTL tests below.
* ``WantsReplyRegistry`` -- a pure, in-memory state machine keyed by
  ``(channel_id, slug)`` with TTL eviction and live-entry compaction.
* ``maybe_trigger_semantic`` -- an async dispatcher that branches on
  ``emoji`` + ``reactor_type``:

  - ``👎`` from a user on an agent-authored message regenerates the reply.
  - ``🙋`` from an agent sets an ephemeral "wants to reply" flag.
  - ``📌`` from an agent on its own message pins (requests a pin) and
    broadcasts an edit affordance.

External collaborators that would reach a live backend (the bridge that
dispatches to an agent, the message store, the chat hub, the cluster
registry) are mocked at the narrowest scope.  The pure in-module logic --
``history_token_budget``, ``build_context_window`` (only where the result must
be pinned deterministically), ``find_agent`` and ``_find_model_manifest`` --
runs for real so tests assert on actual computed values rather than
re-asserting a default they supplied.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.chat.context_window import history_token_budget
from tinyagentos.chat.reactions import (
    WantsReplyRegistry,
    maybe_trigger_semantic,
)


# ---------------------------------------------------------------------------
# Test doubles for the model-manifest lookup collaborators consumed by the
# thumbs-down regenerate branch.  Kept minimal so find_agent/_find_model_manifest
# and history_token_budget run without touching the network or a real registry.
# ---------------------------------------------------------------------------


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


def _state(**attrs):
    """Build a ``MagicMock`` state with explicit attributes.

    Attributes not supplied default to a ``MagicMock`` child (the convention the
    module queries via ``getattr(state, <name>, None)``); callers that need a
    falsy value pass it explicitly, e.g. ``config=None``.
    """
    state = MagicMock()
    for k, v in attrs.items():
        setattr(state, k, v)
    return state


_BASE_MESSAGE = {
    "id": "m1",
    "channel_id": "c1",
    "author_id": "tom",
    "author_type": "agent",
    "content": "bad answer",
    "metadata": {"trace_id": "u1"},
}


# ---------------------------------------------------------------------------
# WantsReplyRegistry -- pure state machine
# ---------------------------------------------------------------------------


def test_registry_list_returns_sorted_alive_slugs():
    r = WantsReplyRegistry()
    r.add("c1", "charlie")
    r.add("c1", "alice")
    r.add("c1", "bob")
    assert r.list("c1") == ["alice", "bob", "charlie"]


def test_registry_list_unknown_channel_returns_empty_list():
    r = WantsReplyRegistry()
    assert r.list("never-seen") == []


def test_registry_add_same_slug_keeps_single_entry():
    r = WantsReplyRegistry()
    r.add("c1", "x")
    r.add("c1", "x")
    assert r.list("c1") == ["x"]


def test_registry_ttl_boundary_exactly_at_ttl_expires(monkeypatch):
    # (now - t) < ttl is strict, so an entry exactly ttl-old is expired.
    r = WantsReplyRegistry(ttl_seconds=60)
    clock = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: clock[0])
    r.add("c1", "x")
    assert r.list("c1") == ["x"]
    clock[0] = 1060.0
    assert r.list("c1") == []


def test_registry_ttl_boundary_just_before_alive(monkeypatch):
    r = WantsReplyRegistry(ttl_seconds=60)
    clock = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: clock[0])
    r.add("c1", "x")
    clock[0] = 1059.999
    assert r.list("c1") == ["x"]


def test_registry_default_ttl_is_300(monkeypatch):
    r = WantsReplyRegistry()
    assert r._ttl == 300
    clock = [0.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: clock[0])
    r.add("c1", "x")
    clock[0] = 299.0
    assert r.list("c1") == ["x"]
    clock[0] = 300.0
    assert r.list("c1") == []


def test_registry_re_add_after_expiry_reactivates(monkeypatch):
    r = WantsReplyRegistry(ttl_seconds=60)
    clock = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: clock[0])
    r.add("c1", "x")
    clock[0] = 1061.0
    assert r.list("c1") == []
    r.add("c1", "x")
    assert r.list("c1") == ["x"]


def test_registry_list_compacts_expired_entries(monkeypatch):
    r = WantsReplyRegistry(ttl_seconds=60)
    clock = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: clock[0])
    r.add("c1", "x")
    clock[0] = 1061.0
    r.list("c1")
    assert r._entries["c1"] == {}


def test_registry_mixed_alive_and_dead_keeps_only_alive(monkeypatch):
    r = WantsReplyRegistry(ttl_seconds=300)
    clock = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: clock[0])
    r.add("c1", "alice")
    clock[0] = 1600.0
    r.add("c1", "bob")
    clock[0] = 1500.0
    assert r.list("c1") == ["bob"]
    assert r._entries["c1"] == {"bob": 1600.0}


def test_registry_isolation_between_channels(monkeypatch):
    r = WantsReplyRegistry()
    r.add("c1", "x")
    r.add("c2", "y")
    assert r.list("c1") == ["x"]
    assert r.list("c2") == ["y"]
    assert r.list("c3") == []


def test_registry_custom_ttl_evicts_earlier_than_default(monkeypatch):
    r = WantsReplyRegistry(ttl_seconds=10)
    clock = [0.0]
    monkeypatch.setattr("tinyagentos.chat.reactions._now", lambda: clock[0])
    r.add("c1", "x")
    clock[0] = 9.0
    assert r.list("c1") == ["x"]
    clock[0] = 10.0
    assert r.list("c1") == []


# ---------------------------------------------------------------------------
# maybe_trigger_semantic -- 👍 👎 regenerate branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thumbs_down_user_on_agent_enqueues_exact_payload():
    """Happy path: user thumbs-down on an agent reply enqueues a regenerate
    message whose payload shape is pinned exactly."""
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(
        return_value={"id": "u1", "content": "what is 2+2?"}
    )
    msg_store.get_messages = AsyncMock(return_value=[])
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window",
        return_value=["__CONTEXT_SENTINEL__"],
    ) as build:
        ret = await maybe_trigger_semantic(
            emoji="👎",
            message=dict(_BASE_MESSAGE),
            reactor_id="user",
            reactor_type="user",
            channel={"id": "c1"},
            state=state,
        )

    assert ret is None
    build.assert_called_once_with([], limit=20, max_tokens=4000)
    bridge.enqueue_user_message.assert_awaited_once()
    call = bridge.enqueue_user_message.await_args
    assert call.args[0] == "tom"
    assert call.args[1] == {
        "id": "u1",
        "trace_id": "u1",
        "channel_id": "c1",
        "from": "user",
        "text": "what is 2+2?",
        "hops_since_user": 0,
        "force_respond": True,
        "regenerate": True,
        "context": ["__CONTEXT_SENTINEL__"],
    }


@pytest.mark.asyncio
async def test_thumbs_down_no_bridge_sessions_is_noop():
    state = _state(
        bridge_sessions=None,
        chat_messages=MagicMock(),
        wants_reply=WantsReplyRegistry(),
    )
    ret = await maybe_trigger_semantic(
        emoji="👎",
        message=dict(_BASE_MESSAGE),
        reactor_id="user",
        reactor_type="user",
        channel={"id": "c1"},
        state=state,
    )
    assert ret is None


@pytest.mark.asyncio
async def test_thumbs_down_from_non_user_reactor_is_noop():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    state = _state(
        bridge_sessions=bridge,
        chat_messages=MagicMock(),
        wants_reply=WantsReplyRegistry(),
    )
    ret = await maybe_trigger_semantic(
        emoji="👎",
        message=dict(_BASE_MESSAGE),
        reactor_id="don",
        reactor_type="agent",
        channel={"id": "c1"},
        state=state,
    )
    assert ret is None
    bridge.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_thumbs_down_on_non_agent_author_is_noop():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    state = _state(
        bridge_sessions=bridge,
        chat_messages=MagicMock(),
        wants_reply=WantsReplyRegistry(),
    )
    msg = dict(_BASE_MESSAGE)
    msg["author_type"] = "user"
    ret = await maybe_trigger_semantic(
        emoji="👎",
        message=msg,
        reactor_id="user",
        reactor_type="user",
        channel={"id": "c1"},
        state=state,
    )
    assert ret is None
    bridge.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_thumbs_down_missing_metadata_falls_back_to_message_id():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(
        return_value={"id": "m1", "content": "original text"}
    )
    msg_store.get_messages = AsyncMock(return_value=[])
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        wants_reply=WantsReplyRegistry(),
    )
    msg = dict(_BASE_MESSAGE)
    del msg["metadata"]

    with patch("tinyagentos.chat.context_window.build_context_window", return_value=[]):
        await maybe_trigger_semantic(
            emoji="👎", message=msg, reactor_id="user", reactor_type="user",
            channel={"id": "c1"}, state=state,
        )

    # trace_id falls back to message id when metadata/trace_id is absent.
    msg_store.get_message.assert_awaited_once_with("m1")
    payload = bridge.enqueue_user_message.await_args.args[1]
    assert payload["trace_id"] == "m1"
    assert payload["text"] == "original text"


@pytest.mark.asyncio
async def test_thumbs_down_get_message_raises_keeps_empty_original_text():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(side_effect=RuntimeError("boom"))
    msg_store.get_messages = AsyncMock(return_value=[])
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        wants_reply=WantsReplyRegistry(),
    )

    with patch("tinyagentos.chat.context_window.build_context_window", return_value=[]):
        await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    payload = bridge.enqueue_user_message.await_args.args[1]
    assert payload["text"] == ""
    assert payload["id"] == "m1"


@pytest.mark.asyncio
async def test_thumbs_down_missing_msg_store_enqueues_empty_text():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    state = _state(
        bridge_sessions=bridge,
        chat_messages=None,
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window", return_value=[]
    ) as build:
        ret = await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    assert ret is None
    build.assert_not_called()
    payload = bridge.enqueue_user_message.await_args.args[1]
    assert payload["text"] == ""
    assert payload["context"] == []
    assert payload["id"] == "m1"
    assert payload["trace_id"] == "u1"


@pytest.mark.asyncio
async def test_thumbs_down_get_messages_raises_gives_empty_context():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(
        return_value={"id": "u1", "content": "ok"}
    )
    msg_store.get_messages = AsyncMock(side_effect=ValueError("nope"))
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window",
        return_value=["should-not-arrive"],
    ):
        await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    payload = bridge.enqueue_user_message.await_args.args[1]
    assert payload["text"] == "ok"
    assert payload["context"] == []


@pytest.mark.asyncio
async def test_thumbs_down_config_none_uses_default_budget():
    """No agent config -> ctx_window 0 -> default history budget (4000)."""
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value={"id": "u1", "content": "hi"})
    msg_store.get_messages = AsyncMock(return_value=[])
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        config=None,
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window", return_value=[]
    ) as build:
        await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    assert build.call_args.kwargs["max_tokens"] == 4000


@pytest.mark.asyncio
async def test_thumbs_down_unknown_model_defaults_budget():
    """Agent declares a model id the registry doesn't know -> manifest None ->
    default budget (4000)."""
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value={"id": "u1", "content": "hi"})
    msg_store.get_messages = AsyncMock(return_value=[])
    config = MagicMock()
    config.agents = [{"name": "tom", "model": "unknown-model"}]
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        config=config,
        registry=_FakeRegistry({}),
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window", return_value=[]
    ) as build:
        await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    assert build.call_args.kwargs["max_tokens"] == 4000


@pytest.mark.asyncio
async def test_thumbs_down_registry_none_defaults_budget():
    """Model is declared but state.registry is None -> manifest None ->
    default budget (4000)."""
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value={"id": "u1", "content": "hi"})
    msg_store.get_messages = AsyncMock(return_value=[])
    config = MagicMock()
    config.agents = [{"name": "tom", "model": "tiny-rkllm"}]
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        config=config,
        registry=None,
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window", return_value=[]
    ) as build:
        await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    assert build.call_args.kwargs["max_tokens"] == 4000


@pytest.mark.asyncio
async def test_thumbs_down_real_model_budget_is_computed():
    """A manifest with a large context window yields the non-floored history
    budget: 16384 - 10000 system - 1024 response = 5360 (> 512 floor)."""
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value={"id": "u1", "content": "hi"})
    msg_store.get_messages = AsyncMock(return_value=[])
    config = MagicMock()
    config.agents = [{"name": "tom", "model": "m1"}]
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        config=config,
        registry=_FakeRegistry({"m1": _FakeManifest(16384)}),
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window", return_value=[]
    ) as build:
        await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    assert build.call_args.kwargs["max_tokens"] == 5360
    # Anchor the literal against the real pure helper so it is not a magic number.
    assert history_token_budget(16384) == 5360


@pytest.mark.asyncio
async def test_thumbs_down_small_context_model_floors_budget_at_512():
    """A tiny context window floors the history budget at 512 (MIN_HISTORY_MAX_TOKENS)."""
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value={"id": "u1", "content": "hi"})
    msg_store.get_messages = AsyncMock(return_value=[])
    config = MagicMock()
    config.agents = [{"name": "tom", "model": "m1"}]
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        config=config,
        registry=_FakeRegistry({"m1": _FakeManifest(4096)}),
        wants_reply=WantsReplyRegistry(),
    )

    with patch(
        "tinyagentos.chat.context_window.build_context_window", return_value=[]
    ) as build:
        await maybe_trigger_semantic(
            emoji="👎", message=dict(_BASE_MESSAGE), reactor_id="user",
            reactor_type="user", channel={"id": "c1"}, state=state,
        )

    assert build.call_args.kwargs["max_tokens"] == 512
    assert history_token_budget(4096) == 512


# ---------------------------------------------------------------------------
# maybe_trigger_semantic -- 🙋 hand-raise branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hand_raise_user_reactor_is_noop():
    state = _state(wants_reply=WantsReplyRegistry())
    ret = await maybe_trigger_semantic(
        emoji="🙋", message=dict(_BASE_MESSAGE), reactor_id="user",
        reactor_type="user", channel={"id": "c1"}, state=state,
    )
    assert ret is None
    assert state.wants_reply.list("c1") == []


@pytest.mark.asyncio
async def test_hand_raise_missing_registry_is_noop():
    """wants_reply absent (None) on state -> early return, no AttributeError."""
    state = _state(wants_reply=None)
    ret = await maybe_trigger_semantic(
        emoji="🙋", message=dict(_BASE_MESSAGE), reactor_id="don",
        reactor_type="agent", channel={"id": "c1"}, state=state,
    )
    assert ret is None


@pytest.mark.asyncio
async def test_hand_raise_agent_sets_wants_reply_for_channel():
    reg = WantsReplyRegistry()
    state = _state(wants_reply=reg)
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "content": "x"}
    ret = await maybe_trigger_semantic(
        emoji="🙋", message=message, reactor_id="don", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )
    assert ret is None
    assert reg.list("c1") == ["don"]


@pytest.mark.asyncio
async def test_hand_raise_same_slug_is_singleton_in_channel():
    reg = WantsReplyRegistry()
    state = _state(wants_reply=reg)
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "content": "x"}
    await maybe_trigger_semantic(
        emoji="🙋", message=message, reactor_id="don", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )
    await maybe_trigger_semantic(
        emoji="🙋", message=message, reactor_id="don", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )
    assert reg.list("c1") == ["don"]


# ---------------------------------------------------------------------------
# maybe_trigger_semantic -- 📌 pin-request branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_request_sets_metadata_and_broadcasts():
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(
        return_value={
            "id": "m1",
            "content": "pinned reply",
            "edited_at": "2026-01-01T00:00:00",
            "metadata": {"foo": "bar"},
        }
    )
    msg_store.set_metadata = AsyncMock()
    hub = MagicMock()
    hub.next_seq = MagicMock(return_value=7)
    hub.broadcast = AsyncMock()
    state = _state(
        chat_messages=msg_store,
        chat_hub=hub,
        wants_reply=WantsReplyRegistry(),
    )
    message = {
        "id": "m1",
        "channel_id": "c1",
        "author_id": "tom",
        "author_type": "agent",
        "metadata": {"foo": "bar"},
    }
    channel = {"id": "c1"}

    ret = await maybe_trigger_semantic(
        emoji="📌", message=message, reactor_id="tom", reactor_type="agent",
        channel=channel, state=state,
    )

    assert ret is None
    msg_store.set_metadata.assert_awaited_once_with(
        "m1", {"foo": "bar", "pin_requested": True}
    )
    hub.broadcast.assert_awaited_once_with("c1", {
        "type": "message_edit",
        "seq": 7,
        "message_id": "m1",
        "content": "pinned reply",
        "edited_at": "2026-01-01T00:00:00",
        "metadata": {"foo": "bar"},
    })


@pytest.mark.asyncio
async def test_pin_request_no_msg_store_is_noop():
    msg_store = MagicMock()
    msg_store.set_metadata = AsyncMock()
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    state = _state(
        chat_messages=None,
        chat_hub=hub,
        wants_reply=WantsReplyRegistry(),
    )
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "metadata": {}}

    ret = await maybe_trigger_semantic(
        emoji="📌", message=message, reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )

    assert ret is None
    msg_store.set_metadata.assert_not_awaited()
    hub.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_pin_request_no_hub_skips_broadcast():
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value=None)
    msg_store.set_metadata = AsyncMock()
    state = _state(
        chat_messages=msg_store,
        chat_hub=None,
        wants_reply=WantsReplyRegistry(),
    )
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "metadata": {"foo": "bar"}}

    ret = await maybe_trigger_semantic(
        emoji="📌", message=message, reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )

    assert ret is None
    msg_store.set_metadata.assert_awaited_once_with("m1", {"foo": "bar", "pin_requested": True})
    assert state.chat_hub is None


@pytest.mark.asyncio
async def test_pin_request_get_message_none_skips_broadcast():
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(return_value=None)
    msg_store.set_metadata = AsyncMock()
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    hub.next_seq = MagicMock(return_value=1)
    state = _state(
        chat_messages=msg_store,
        chat_hub=hub,
        wants_reply=WantsReplyRegistry(),
    )
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "metadata": {"foo": "bar"}}

    ret = await maybe_trigger_semantic(
        emoji="📌", message=message, reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )

    assert ret is None
    msg_store.set_metadata.assert_awaited_once()
    hub.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_pin_request_non_author_is_noop():
    msg_store = MagicMock()
    msg_store.set_metadata = AsyncMock()
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    state = _state(
        chat_messages=msg_store,
        chat_hub=hub,
        wants_reply=WantsReplyRegistry(),
    )
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "metadata": {}}

    ret = await maybe_trigger_semantic(
        emoji="📌", message=message, reactor_id="don", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )

    assert ret is None
    msg_store.set_metadata.assert_not_awaited()
    hub.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_pin_request_none_metadata_becomes_empty_dict():
    msg_store = MagicMock()
    msg_store.set_metadata = AsyncMock()
    msg_store.get_message = AsyncMock(return_value=None)
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    hub.next_seq = MagicMock(return_value=1)
    state = _state(
        chat_messages=msg_store,
        chat_hub=hub,
        wants_reply=WantsReplyRegistry(),
    )
    message = {"id": "m1", "channel_id": "c1", "author_id": "tom",
               "author_type": "agent", "metadata": None}

    ret = await maybe_trigger_semantic(
        emoji="📌", message=message, reactor_id="tom", reactor_type="agent",
        channel={"id": "c1"}, state=state,
    )

    assert ret is None
    msg_store.set_metadata.assert_awaited_once_with("m1", {"pin_requested": True})


@pytest.mark.asyncio
async def test_pin_request_without_channel_id_falls_back_to_channel_id():
    """When the message carries no channel_id, broadcast uses channel['id']."""
    msg_store = MagicMock()
    msg_store.get_message = AsyncMock(
        return_value={"id": "m1", "content": "c", "edited_at": "t",
                      "metadata": {"pin_requested": True}}
    )
    msg_store.set_metadata = AsyncMock()
    hub = MagicMock()
    hub.next_seq = MagicMock(return_value=1)
    hub.broadcast = AsyncMock()
    state = _state(
        chat_messages=msg_store,
        chat_hub=hub,
        wants_reply=WantsReplyRegistry(),
    )
    message = {"id": "m1", "author_id": "tom",
               "author_type": "agent", "metadata": {}}

    await maybe_trigger_semantic(
        emoji="📌", message=message, reactor_id="tom", reactor_type="agent",
        channel={"id": "chan-from-route"}, state=state,
    )

    assert hub.broadcast.await_args.args[0] == "chan-from-route"


# ---------------------------------------------------------------------------
# maybe_trigger_semantic -- decorative reactions are no-ops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decorative_reaction_is_noop():
    bridge = MagicMock()
    bridge.enqueue_user_message = AsyncMock()
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    msg_store = MagicMock()
    msg_store.set_metadata = AsyncMock()
    state = _state(
        bridge_sessions=bridge,
        chat_messages=msg_store,
        chat_hub=hub,
        wants_reply=WantsReplyRegistry(),
    )
    ret = await maybe_trigger_semantic(
        emoji="🔥", message=dict(_BASE_MESSAGE), reactor_id="user",
        reactor_type="user", channel={"id": "c1"}, state=state,
    )
    assert ret is None
    bridge.enqueue_user_message.assert_not_awaited()
    hub.broadcast.assert_not_awaited()
    msg_store.set_metadata.assert_not_awaited()
