"""Chat router delivery to registry guest agents and /api/agents listing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from tinyagentos.agent_chat_router import AgentChatRouter
from tinyagentos.chat.guest_agents import list_guests, resolve_guest


class _FakeBridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def enqueue_user_message(self, slug: str, msg: dict) -> None:
        self.calls.append((slug, msg))


@pytest_asyncio.fixture
async def stores(app, tmp_data_dir):
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    yield app.state
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


async def _register(state, name, *, scope="chat_session", expires_at=None, suspend=False):
    rec = await state.agent_registry.register(
        framework="taosmd", display_name=name, origin="taos-deployed",
        handle=f"@{name}",
    )
    cid = rec["canonical_id"]
    if scope:
        await state.agent_grants.add_grant(cid, scope, expires_at=expires_at)
    if suspend:
        await state.agent_registry.set_status(cid, "suspended")
    return cid


def _router_state(real, config_agents, bridge):
    state = MagicMock()
    state.config = MagicMock()
    state.config.agents = config_agents
    state.chat_messages = MagicMock()
    state.chat_messages.send_message = AsyncMock(return_value={
        "id": "s1", "channel_id": "c1", "author_id": "x",
        "author_type": "agent", "content": "", "created_at": 1.0,
    })
    state.chat_messages.get_messages = AsyncMock(return_value=[])
    state.chat_channels = MagicMock()
    state.chat_channels.update_last_message_at = AsyncMock()
    state.chat_hub = MagicMock()
    state.chat_hub.broadcast = AsyncMock()
    state.chat_hub.next_seq = MagicMock(return_value=1)
    state.bridge_sessions = bridge
    state.agent_registry = real.agent_registry
    state.agent_grants = real.agent_grants
    return state


_MSG = {
    "id": "m1", "channel_id": "c1", "author_id": "user",
    "author_type": "user", "content": "hello", "created_at": 1.0,
}


async def _dm(state, slug):
    await AgentChatRouter(state)._route(
        dict(_MSG), {"id": "c1", "type": "dm", "members": ["user", slug]},
    )


@pytest.mark.asyncio
class TestRouterGuests:
    async def test_dm_to_guest_enqueues_once_no_system_reply(self, stores):
        await _register(stores, "visitor")
        bridge = _FakeBridge()
        state = _router_state(stores, [], bridge)
        await _dm(state, "visitor")
        assert len(bridge.calls) == 1
        slug, msg = bridge.calls[0]
        assert slug == "visitor"
        assert msg["text"] == "hello"
        assert msg["force_respond"] is True
        state.chat_messages.send_message.assert_not_awaited()

    async def test_no_grant_not_delivered(self, stores):
        await _register(stores, "nogrant", scope=None)
        bridge = _FakeBridge()
        state = _router_state(stores, [], bridge)
        await _dm(state, "nogrant")
        assert bridge.calls == []
        state.chat_messages.send_message.assert_not_awaited()

    async def test_other_scope_not_delivered(self, stores):
        await _register(stores, "wrongscope", scope="a2a_receive")
        bridge = _FakeBridge()
        state = _router_state(stores, [], bridge)
        await _dm(state, "wrongscope")
        assert bridge.calls == []

    async def test_expired_grant_not_delivered(self, stores):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await _register(stores, "expired", expires_at=past)
        bridge = _FakeBridge()
        state = _router_state(stores, [], bridge)
        await _dm(state, "expired")
        assert bridge.calls == []

    async def test_suspended_not_delivered(self, stores):
        await _register(stores, "sleeper", suspend=True)
        bridge = _FakeBridge()
        state = _router_state(stores, [], bridge)
        await _dm(state, "sleeper")
        assert bridge.calls == []
        state.chat_messages.send_message.assert_not_awaited()

    async def test_config_agent_wins_over_guest(self, stores):
        await _register(stores, "dup")
        bridge = _FakeBridge()
        state = _router_state(
            stores, [{"name": "dup", "status": "stopped"}], bridge,
        )
        await _dm(state, "dup")
        # Config path: not running -> system reply, no enqueue.
        assert bridge.calls == []
        state.chat_messages.send_message.assert_awaited_once()

    async def test_helpers_empty_without_stores(self):
        state = MagicMock(spec=[])
        assert await resolve_guest(state, "x") is None
        assert await list_guests(state) == []


@pytest.mark.asyncio
class TestListAgentsGuests:
    async def test_default_unchanged_and_include_guests(self, client, app, stores):
        await _register(app.state, "visitor")
        await _register(app.state, "dup")
        app.state.config.agents = [{"name": "dup", "model": "m", "status": "running"}]

        base = await client.get("/api/agents")
        assert base.status_code == 200
        assert base.json() == [{"name": "dup", "model": "m", "status": "running"}]

        resp = await client.get("/api/agents?include_guests=1")
        assert resp.status_code == 200
        data = resp.json()
        assert [a["name"] for a in data].count("dup") == 1
        guest = next(a for a in data if a["name"] == "visitor")
        assert guest["guest"] is True
        assert "model" not in guest and "llm_key" not in guest
        cfg = next(a for a in data if a["name"] == "dup")
        assert "guest" not in cfg
