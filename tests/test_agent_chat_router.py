import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.agent_chat_router import AgentChatRouter


class _FakeBridge:
    """Duck-type BridgeSessionRegistry with call tracking."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def enqueue_user_message(self, slug: str, msg: dict) -> None:
        self.calls.append((slug, msg))


def _state_for(agent_record: dict | None, *, bridge: _FakeBridge | None = None):
    state = MagicMock()
    state.config = MagicMock()
    state.config.agents = [agent_record] if agent_record else []
    state.chat_messages = MagicMock()
    state.chat_messages.send_message = AsyncMock(return_value={
        "id": "m1", "channel_id": "c1",
        "author_id": "openclaw", "author_type": "agent",
        "content": "", "created_at": 1.0,
    })
    state.chat_messages.get_messages = AsyncMock(return_value=[])
    state.chat_channels = MagicMock()
    state.chat_channels.update_last_message_at = AsyncMock()
    state.chat_hub = MagicMock()
    state.chat_hub.broadcast = AsyncMock()
    state.chat_hub.next_seq = MagicMock(return_value=1)
    # bridge_sessions is set as an attribute; absence simulates misconfigured host
    if bridge is not None:
        state.bridge_sessions = bridge
    else:
        # Simulate missing attribute (not just None)
        del state.bridge_sessions
    return state


def _channel(members, mode="quiet", muted=None, ctype="group"):
    return {
        "id": "c1",
        "type": ctype,
        "members": members,
        "settings": {
            "response_mode": mode,
            "max_hops": 3,
            "cooldown_seconds": 5,
            "rate_cap_per_minute": 20,
            "muted": muted or [],
        },
    }


class TestAgentChatRouter:
    @pytest.mark.asyncio
    async def test_enqueues_to_bridge_when_agent_running(self):
        bridge = _FakeBridge()
        agent = {"name": "openclaw", "status": "running"}
        state = _state_for(agent, bridge=bridge)

        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "hello",
            "created_at": 1.0,
        }
        # DM channel: 2-member user+agent, type=dm triggers force-respond path
        channel = {"id": "c1", "type": "dm", "members": ["user", "openclaw"]}
        await router._route(message, channel)

        assert len(bridge.calls) == 1
        slug, enqueued = bridge.calls[0]
        assert slug == "openclaw"
        assert enqueued["text"] == "hello"
        assert enqueued["from"] == "user"
        assert enqueued["trace_id"] == "m1"
        assert enqueued["channel_id"] == "c1"
        # System-reply path must NOT have been triggered.
        state.chat_messages.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_non_user_messages(self):
        bridge = _FakeBridge()
        state = _state_for({"name": "openclaw", "status": "running"}, bridge=bridge)
        router = AgentChatRouter(state)
        # agent-authored message on a quiet group channel with no mention → no fanout
        message = {"author_id": "openclaw", "author_type": "agent", "content": "self-talk",
                   "metadata": {"hops_since_user": 0}}
        router.dispatch(message, {"id": "c1", "type": "group", "members": ["user", "openclaw"],
                                  "settings": {"response_mode": "quiet", "max_hops": 3,
                                               "cooldown_seconds": 5, "rate_cap_per_minute": 20,
                                               "muted": []}})
        await asyncio.sleep(0.01)
        assert bridge.calls == []
        state.chat_messages.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_agent_record_is_noop(self):
        bridge = _FakeBridge()
        state = _state_for(None, bridge=bridge)
        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "hi",
            "metadata": {"hops_since_user": 0},
        }
        channel = {"id": "c1", "type": "dm", "members": ["user", "ghost"]}
        await router._route(message, channel)
        assert bridge.calls == []
        state.chat_messages.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_running_agent_posts_system_reply(self):
        bridge = _FakeBridge()
        agent = {"name": "openclaw", "status": "deploying"}
        state = _state_for(agent, bridge=bridge)
        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "hi",
            "metadata": {"hops_since_user": 0},
        }
        channel = {"id": "c1", "type": "dm", "members": ["user", "openclaw"]}
        await router._route(message, channel)

        assert bridge.calls == []
        state.chat_messages.send_message.assert_awaited_once()
        call = state.chat_messages.send_message.call_args.kwargs
        assert "not running" in call["content"]

    @pytest.mark.asyncio
    async def test_missing_bridge_registry_posts_system_reply(self):
        # bridge=None means bridge_sessions attribute is absent on state
        agent = {"name": "openclaw", "status": "running"}
        state = _state_for(agent, bridge=None)
        router = AgentChatRouter(state)
        message = {
            "id": "m1", "channel_id": "c1", "author_id": "user",
            "author_type": "user", "content": "hi",
            "metadata": {"hops_since_user": 0},
        }
        channel = {"id": "c1", "type": "dm", "members": ["user", "openclaw"]}
        await router._route(message, channel)

        state.chat_messages.send_message.assert_awaited_once()
        call = state.chat_messages.send_message.call_args.kwargs
        assert "bridge registry" in call["content"]


@pytest.mark.asyncio
async def test_quiet_no_mention_no_fanout():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m1", "author_id": "user", "author_type": "user",
               "content": "hi folks", "metadata": {"hops_since_user": 0}}
    await router._route(message, _channel(["user", "tom", "don"], "quiet"))
    assert bridge.calls == []


@pytest.mark.asyncio
async def test_quiet_with_mention_routes_only_to_mentioned():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m2", "author_id": "user", "author_type": "user",
               "content": "@tom ping", "metadata": {"hops_since_user": 0}}
    await router._route(message, _channel(["user", "tom", "don"], "quiet"))
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["tom"]
    assert bridge.calls[0][1]["force_respond"] is True


@pytest.mark.asyncio
async def test_lively_fans_out_to_all_others_without_force():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m3", "author_id": "user", "author_type": "user",
               "content": "anyone there?", "metadata": {"hops_since_user": 0}}
    await router._route(message, _channel(["user", "tom", "don"], "lively"))
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["don", "tom"]
    assert all(c[1]["force_respond"] is False for c in bridge.calls)


@pytest.mark.asyncio
async def test_muted_agent_skipped():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m4", "author_id": "user", "author_type": "user",
               "content": "hi", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom", "don"], "lively", muted=["tom"])
    await router._route(message, ch)
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["don"]


@pytest.mark.asyncio
async def test_hop_cap_stops_chain():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    # Agent-authored at hops=3 -> next_hops=4 > max_hops(3) -> drop (no mention)
    message = {"id": "m5", "author_id": "tom", "author_type": "agent",
               "content": "still there", "metadata": {"hops_since_user": 3}}
    await router._route(message, _channel(["user", "tom", "don"], "lively"))
    assert bridge.calls == []


@pytest.mark.asyncio
async def test_hop_cap_overridden_by_mention():
    bridge = _FakeBridge()
    state = _state_for({"name": "don", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    message = {"id": "m6", "author_id": "tom", "author_type": "agent",
               "content": "@don please chime in", "metadata": {"hops_since_user": 5}}
    await router._route(message, _channel(["user", "tom", "don"], "lively"))
    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["don"]
    assert bridge.calls[0][1]["force_respond"] is True


@pytest.mark.asyncio
async def test_at_all_resets_and_forces_everyone():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"},
                           {"name": "don", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m", "author_id": "user", "author_type": "user",
           "content": "@all wake up", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom", "don"], "lively")
    await router._route(msg, ch)
    assert sorted(c[0] for c in bridge.calls) == ["don", "tom"]
    assert all(c[1]["force_respond"] is True for c in bridge.calls)


@pytest.mark.asyncio
async def test_cooldown_blocks_subsequent_unforced():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "hi", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom"], "lively")
    await router._route(msg, ch)
    # Second message, no mention: cooldown applies
    msg2 = {**msg, "id": "m2", "content": "again"}
    await router._route(msg2, ch)
    # Only one enqueue because the second was blocked
    assert len(bridge.calls) == 1


@pytest.mark.asyncio
async def test_cooldown_skipped_when_mentioned():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "hi", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom"], "lively")
    await router._route(msg, ch)
    msg2 = {**msg, "id": "m2", "content": "@tom still there?"}
    await router._route(msg2, ch)
    assert len(bridge.calls) == 2
    assert bridge.calls[1][1]["force_respond"] is True


@pytest.mark.asyncio
async def test_dm_always_forces_respond():
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)
    msg = {"id": "m", "author_id": "user", "author_type": "user",
           "content": "ping", "metadata": {"hops_since_user": 0}}
    ch = _channel(["user", "tom"], mode="quiet", ctype="dm")
    await router._route(msg, ch)
    assert len(bridge.calls) == 1
    assert bridge.calls[0][1]["force_respond"] is True


@pytest.mark.asyncio
async def test_router_uses_thread_resolver_when_thread_id_present():
    """A message with thread_id set goes through threads.resolve_thread_recipients,
    skipping the channel fanout path."""
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "tom", "status": "running"},
        {"name": "don", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    # chat_messages needs get_message + get_thread_messages for the resolver
    state.chat_messages.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    state.chat_messages.get_thread_messages = AsyncMock(return_value=[])
    router = AgentChatRouter(state)
    message = {
        "id": "m1", "author_id": "user", "author_type": "user",
        "content": "thoughts?", "thread_id": "p1",
        "metadata": {"hops_since_user": 0},
    }
    await router._route(message, _channel(["user", "tom", "don"], "quiet"))
    slugs = sorted(c[0] for c in bridge.calls)
    # tom is the parent author → recipient; don is not mentioned + not prior replier → skipped.
    assert slugs == ["tom"]


@pytest.mark.asyncio
async def test_router_thread_policy_key_is_scoped():
    """Policy key used in thread routing should be channel_id:thread:<id>,
    so a thread doesn't consume the channel's rate cap or block unrelated
    channel messages."""
    bridge = _FakeBridge()
    state = _state_for({"name": "tom", "status": "running"}, bridge=bridge)
    state.config.agents = [{"name": "tom", "status": "running"}]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    state.chat_messages.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tom", "author_type": "agent",
    })
    state.chat_messages.get_thread_messages = AsyncMock(return_value=[])
    router = AgentChatRouter(state)
    # Route a thread message.
    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "go", "thread_id": "p1",
           "metadata": {"hops_since_user": 0}}
    await router._route(msg, _channel(["user", "tom"], "quiet"))
    # The policy should have recorded a send keyed "c1:thread:p1" — check by trying to
    # route a channel-scope message next; it should NOT be rate-limited by the thread send.
    msg2 = {"id": "m2", "author_id": "user", "author_type": "user",
            "content": "channel msg", "metadata": {"hops_since_user": 0}}
    await router._route(msg2, _channel(["user", "tom"], "lively"))
    # Expect 2 bridge calls total (one per message), since policy keys are independent.
    assert len(bridge.calls) == 2


# ---------------------------------------------------------------------------
# Lead agent routing
# ---------------------------------------------------------------------------

def _channel_with_leads(members, leads, mode="quiet", muted=None, ctype="group"):
    return {
        "id": "c1",
        "type": ctype,
        "members": members,
        "settings": {
            "response_mode": mode,
            "leads": leads,
            "max_hops": 3,
            "cooldown_seconds": 5,
            "rate_cap_per_minute": 20,
            "muted": muted or [],
        },
    }


@pytest.mark.asyncio
async def test_lead_receives_quiet_message_not_mentioning_them():
    """A lead agent receives messages in quiet mode even without an @mention."""
    bridge = _FakeBridge()
    state = _state_for({"name": "coord", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "coord", "status": "running"},
        {"name": "worker", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)

    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "just an update", "metadata": {"hops_since_user": 0}}
    ch = _channel_with_leads(["user", "coord", "worker"], leads=["coord"], mode="quiet")
    await router._route(msg, ch)

    slugs = [c[0] for c in bridge.calls]
    assert "coord" in slugs
    assert "worker" not in slugs


@pytest.mark.asyncio
async def test_non_lead_skipped_in_quiet_with_no_mention():
    """Non-lead agents are still silent in quiet mode when not mentioned."""
    bridge = _FakeBridge()
    state = _state_for({"name": "coord", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "coord", "status": "running"},
        {"name": "worker", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)

    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "just an update", "metadata": {"hops_since_user": 0}}
    ch = _channel_with_leads(["user", "coord", "worker"], leads=["coord"], mode="quiet")
    await router._route(msg, ch)

    slugs = [c[0] for c in bridge.calls]
    assert "worker" not in slugs


@pytest.mark.asyncio
async def test_multiple_leads_both_receive():
    """All leads receive a quiet message not mentioning either of them."""
    bridge = _FakeBridge()
    state = _state_for({"name": "alpha", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "alpha", "status": "running"},
        {"name": "beta", "status": "running"},
        {"name": "worker", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)

    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "status update", "metadata": {"hops_since_user": 0}}
    ch = _channel_with_leads(["user", "alpha", "beta", "worker"],
                              leads=["alpha", "beta"], mode="quiet")
    await router._route(msg, ch)

    slugs = sorted(c[0] for c in bridge.calls)
    assert slugs == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_lead_not_double_added_when_mentioned():
    """Lead @mentioned explicitly: they appear exactly once in recipients."""
    bridge = _FakeBridge()
    state = _state_for({"name": "coord", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "coord", "status": "running"},
        {"name": "worker", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)

    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "@coord check this", "metadata": {"hops_since_user": 0}}
    ch = _channel_with_leads(["user", "coord", "worker"], leads=["coord"], mode="quiet")
    await router._route(msg, ch)

    slugs = [c[0] for c in bridge.calls]
    assert slugs.count("coord") == 1


@pytest.mark.asyncio
async def test_lead_who_is_author_does_not_receive_own_message():
    """A lead who authored the message does not receive it."""
    bridge = _FakeBridge()
    state = _state_for({"name": "coord", "status": "running"}, bridge=bridge)
    state.config.agents = [
        {"name": "coord", "status": "running"},
        {"name": "worker", "status": "running"},
    ]
    from tinyagentos.chat.group_policy import GroupPolicy
    state.group_policy = GroupPolicy()
    router = AgentChatRouter(state)

    msg = {"id": "m1", "author_id": "coord", "author_type": "agent",
           "content": "progress report", "metadata": {"hops_since_user": 1}}
    ch = _channel_with_leads(["user", "coord", "worker"], leads=["coord"], mode="quiet")
    await router._route(msg, ch)

    slugs = [c[0] for c in bridge.calls]
    assert "coord" not in slugs
