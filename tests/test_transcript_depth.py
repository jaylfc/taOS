from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.agent_messages import AgentMessageStore


@pytest_asyncio.fixture
async def msg_store(tmp_path):
    store = AgentMessageStore(tmp_path / "messages.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestTranscriptDepth:
    async def test_send_with_reasoning(self, msg_store):
        msg_id = await msg_store.send(
            "naira", "kira", "Analysis complete",
            tool_calls=[{"name": "search", "args": {"q": "test"}}],
            tool_results=[{"output": "found"}],
            reasoning="I need to search first, then summarize.",
            depth=3,
        )
        assert msg_id is not None

    async def test_depth_1_hides_tools_and_reasoning(self, msg_store):
        await msg_store.send(
            "naira", "kira", "Hello",
            tool_calls=[{"name": "search"}],
            reasoning="Thinking...",
            depth=3,
        )
        messages = await msg_store.get_messages("naira", depth=1)
        assert len(messages) == 1
        msg = messages[0]
        assert msg["message"] == "Hello"
        assert msg["tool_calls"] == []
        assert msg["tool_results"] == []
        assert msg["reasoning"] == ""

    async def test_depth_2_shows_tools_hides_reasoning(self, msg_store):
        await msg_store.send(
            "naira", "kira", "Hello",
            tool_calls=[{"name": "search"}],
            reasoning="Thinking...",
            depth=3,
        )
        messages = await msg_store.get_messages("naira", depth=2)
        msg = messages[0]
        assert msg["message"] == "Hello"
        assert len(msg["tool_calls"]) == 1
        assert msg["reasoning"] == ""

    async def test_depth_3_shows_everything(self, msg_store):
        await msg_store.send(
            "naira", "kira", "Hello",
            tool_calls=[{"name": "search"}],
            tool_results=[{"output": "found"}],
            reasoning="Thinking deeply...",
            depth=3,
        )
        messages = await msg_store.get_messages("naira", depth=3)
        msg = messages[0]
        assert msg["message"] == "Hello"
        assert len(msg["tool_calls"]) == 1
        assert len(msg["tool_results"]) == 1
        assert msg["reasoning"] == "Thinking deeply..."

    async def test_default_depth_is_2(self, msg_store):
        await msg_store.send("naira", "kira", "Hi")
        messages = await msg_store.get_messages("naira")
        msg = messages[0]
        assert msg["depth"] == 2

    async def test_conversation_respects_depth(self, msg_store):
        await msg_store.send(
            "naira", "kira", "msg1",
            tool_calls=[{"name": "tool1"}],
            reasoning="reason1",
        )
        await msg_store.send(
            "kira", "naira", "msg2",
            tool_calls=[{"name": "tool2"}],
            reasoning="reason2",
        )
        # Depth 1 - no tools/reasoning
        convo = await msg_store.get_conversation("naira", "kira", depth=1)
        assert len(convo) == 2
        for m in convo:
            assert m["tool_calls"] == []
            assert m["reasoning"] == ""

        # Depth 3 - everything
        convo = await msg_store.get_conversation("naira", "kira", depth=3)
        assert convo[0]["tool_calls"] == [{"name": "tool1"}]
        assert convo[0]["reasoning"] == "reason1"

    async def test_message_stores_depth_field(self, msg_store):
        await msg_store.send("naira", "kira", "depth1", depth=1)
        await msg_store.send("naira", "kira", "depth3", depth=3)
        messages = await msg_store.get_messages("naira", depth=3)
        depths = {m["message"]: m["depth"] for m in messages}
        assert depths["depth1"] == 1
        assert depths["depth3"] == 3


# --- Route tests for transcript depth ---

@pytest.mark.asyncio
async def test_api_messages_with_depth_param(client):
    # Send a message with reasoning
    await client.post("/api/agents/test-agent/messages", json={
        "from_agent": "other-agent",
        "message": "Analysis done",
        "tool_calls": [{"name": "search"}],
        "reasoning": "I searched first",
        "depth": 3,
    })

    # Depth 1: no tools or reasoning
    resp = await client.get("/api/agents/test-agent/messages?depth=1")
    assert resp.status_code == 200
    msg = resp.json()[0]
    assert msg["message"] == "Analysis done"
    assert msg["tool_calls"] == []
    assert msg["reasoning"] == ""

    # Depth 3: everything
    resp = await client.get("/api/agents/test-agent/messages?depth=3")
    msg = resp.json()[0]
    assert msg["tool_calls"] == [{"name": "search"}]
    assert msg["reasoning"] == "I searched first"


