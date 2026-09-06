"""End-to-end: posting `@<slug>` in an A2A channel routes through
agent_chat_router to the addressed agent's bridge queue.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tinyagentos.agent_chat_router import AgentChatRouter


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def enqueue_user_message(self, agent_name: str, payload: dict) -> None:
        self.calls.append((agent_name, payload))


@pytest.mark.asyncio
async def test_at_mention_in_a2a_channel_routes_to_agent_b():
    bridge = _FakeBridge()
    state = SimpleNamespace(
        config=SimpleNamespace(
            agents=[
                {"name": "agentA", "status": "running"},
                {"name": "agentB", "status": "running"},
            ],
        ),
        bridge_sessions=bridge,
        group_policy=None,
    )

    router = AgentChatRouter(state)

    channel = {
        "id": "ch_a2a",
        "type": "group",
        "members": ["agentA", "agentB"],
        "settings": {"kind": "a2a", "max_hops": 3, "muted": []},
        "project_id": "prj_1",
    }
    message = {
        "id": "msg_1",
        "channel_id": "ch_a2a",
        "author_id": "agentA",
        "content_type": "text",
        "content": "@agentB please continue",
        "metadata": {"hops_since_user": 0},
        "created_at": 1.0,
    }

    await router._route_inner(message, channel)

    assert len(bridge.calls) == 1
    target, payload = bridge.calls[0]
    assert target == "agentB"
    assert payload["text"] == "@agentB please continue"
    assert payload["channel_id"] == "ch_a2a"
    assert payload["force_respond"] is True
