"""Unit tests for AgentMessageStore send/list/ordering."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tinyagentos.agent_messages import AgentMessageStore


async def _store(tmp_path):
    s = AgentMessageStore(tmp_path / "agent_messages.db")
    await s.init()
    return s


@pytest.mark.asyncio
async def test_send_persists_message_and_returns_id(tmp_path):
    store = await _store(tmp_path)
    try:
        msg_id = await store.send("alpha", "beta", "hello there")
        assert isinstance(msg_id, int)
        assert msg_id > 0

        messages = await store.get_messages("alpha")
        assert len(messages) == 1
        assert messages[0]["id"] == msg_id
        assert messages[0]["from"] == "alpha"
        assert messages[0]["to"] == "beta"
        assert messages[0]["message"] == "hello there"
        assert messages[0]["read"] is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_messages_includes_sent_and_received(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "alpha to beta")
        await store.send("beta", "alpha", "beta to alpha")
        await store.send("gamma", "delta", "unrelated")

        alpha_msgs = await store.get_messages("alpha")
        assert len(alpha_msgs) == 2
        bodies = {m["message"] for m in alpha_msgs}
        assert bodies == {"alpha to beta", "beta to alpha"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_messages_orders_by_timestamp_desc(tmp_path):
    store = await _store(tmp_path)
    try:
        times = iter([100.0, 200.0, 300.0])
        with patch("tinyagentos.agent_messages.time.time", side_effect=lambda: next(times)):
            await store.send("alpha", "beta", "oldest")
            await store.send("alpha", "beta", "middle")
            await store.send("alpha", "beta", "newest")

        messages = await store.get_messages("alpha")
        assert [m["message"] for m in messages] == ["newest", "middle", "oldest"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_conversation_orders_by_timestamp_asc(tmp_path):
    store = await _store(tmp_path)
    try:
        times = iter([50.0, 150.0, 250.0])
        with patch("tinyagentos.agent_messages.time.time", side_effect=lambda: next(times)):
            await store.send("alpha", "beta", "first")
            await store.send("beta", "alpha", "second")
            await store.send("alpha", "gamma", "other thread")

        convo = await store.get_conversation("alpha", "beta")
        assert [m["message"] for m in convo] == ["first", "second"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_messages_respects_limit(tmp_path):
    store = await _store(tmp_path)
    try:
        for i in range(5):
            await store.send("alpha", "beta", f"msg-{i}")

        messages = await store.get_messages("alpha", limit=2)
        assert len(messages) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_send_with_all_optional_params(tmp_path):
    store = await _store(tmp_path)
    try:
        tool_calls = [{"name": "search", "args": {"q": "test"}}]
        tool_results = [{"output": "result"}]
        metadata = {"session": "abc", "priority": 1}
        msg_id = await store.send(
            "alpha", "beta", "full message",
            tool_calls=tool_calls, tool_results=tool_results,
            reasoning="I think therefore I am", depth=3, metadata=metadata,
        )
        msgs = await store.get_messages("alpha", depth=3)
        assert len(msgs) == 1
        m = msgs[0]
        assert m["message"] == "full message"
        assert m["tool_calls"] == tool_calls
        assert m["tool_results"] == tool_results
        assert m["reasoning"] == "I think therefore I am"
        assert m["metadata"] == metadata
        assert m["depth"] == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_send_defaults_for_optional_params(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "minimal")
        msgs = await store.get_messages("alpha")
        m = msgs[0]
        assert m["tool_calls"] == []
        assert m["tool_results"] == []
        assert m["reasoning"] == ""
        assert m["metadata"] == {}
        assert m["depth"] == 2
    finally:
        await store.close()


def test_format_message_depth1():
    row = (1, "a", "b", "hi", '[{"name":"tc"}]', '[{"out":"tr"}]', "some reasoning", 3, '{"k":"v"}', 100.0, 0)
    msg = AgentMessageStore._format_message(row, view_depth=1)
    assert msg["message"] == "hi"
    assert msg["tool_calls"] == []
    assert msg["tool_results"] == []
    assert msg["reasoning"] == ""
    assert msg["metadata"] == {"k": "v"}
    assert msg["timestamp"] == 100.0
    assert msg["read"] is False


def test_format_message_depth2():
    row = (1, "a", "b", "hi", '[{"name":"tc"}]', '[{"out":"tr"}]', "some reasoning", 3, '{}', 100.0, 1)
    msg = AgentMessageStore._format_message(row, view_depth=2)
    assert msg["tool_calls"] == [{"name": "tc"}]
    assert msg["tool_results"] == [{"out": "tr"}]
    assert msg["reasoning"] == ""
    assert msg["read"] is True


def test_format_message_depth3():
    row = (1, "a", "b", "hi", '[]', '[]', "deep thought", 3, '{}', 100.0, 0)
    msg = AgentMessageStore._format_message(row, view_depth=3)
    assert msg["reasoning"] == "deep thought"
    assert msg["tool_calls"] == []
    assert msg["tool_results"] == []


@pytest.mark.asyncio
async def test_get_messages_depth_filters_fields(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send(
            "alpha", "beta", "test",
            tool_calls=[{"name": "x"}], tool_results=[{"out": "y"}],
            reasoning="because",
        )
        d1 = await store.get_messages("alpha", depth=1)
        assert d1[0]["tool_calls"] == []
        assert d1[0]["tool_results"] == []
        assert d1[0]["reasoning"] == ""

        d2 = await store.get_messages("alpha", depth=2)
        assert d2[0]["tool_calls"] == [{"name": "x"}]
        assert d2[0]["tool_results"] == [{"out": "y"}]
        assert d2[0]["reasoning"] == ""

        d3 = await store.get_messages("alpha", depth=3)
        assert d3[0]["reasoning"] == "because"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_conversation_depth_filters_fields(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send(
            "alpha", "beta", "test",
            tool_calls=[{"name": "x"}], reasoning="because",
        )
        d1 = await store.get_conversation("alpha", "beta", depth=1)
        assert d1[0]["tool_calls"] == []
        assert d1[0]["reasoning"] == ""

        d3 = await store.get_conversation("alpha", "beta", depth=3)
        assert d3[0]["tool_calls"] == [{"name": "x"}]
        assert d3[0]["reasoning"] == "because"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_conversation_is_bidirectional(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "a->b")
        await store.send("beta", "alpha", "b->a")
        await store.send("alpha", "gamma", "a->g")

        convo = await store.get_conversation("alpha", "beta")
        assert len(convo) == 2
        bodies = {m["message"] for m in convo}
        assert bodies == {"a->b", "b->a"}

        convo2 = await store.get_conversation("beta", "alpha")
        assert len(convo2) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_conversation_respects_limit(tmp_path):
    store = await _store(tmp_path)
    try:
        for i in range(5):
            await store.send("alpha", "beta", f"msg-{i}")

        convo = await store.get_conversation("alpha", "beta", limit=3)
        assert len(convo) == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_contacts(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "a->b 1")
        await store.send("alpha", "beta", "a->b 2")
        await store.send("beta", "alpha", "b->a")
        await store.send("gamma", "alpha", "g->a")
        await store.send("alpha", "gamma", "a->g")

        contacts = await store.get_contacts("alpha")
        contact_map = {c["name"]: c["unread_count"] for c in contacts}
        assert "beta" in contact_map
        assert "gamma" in contact_map
        # beta sent 1 unread to alpha, gamma sent 1 unread to alpha
        assert contact_map["beta"] == 1
        assert contact_map["gamma"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_contacts_no_unread_when_all_read(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("beta", "alpha", "b->a")
        await store.mark_read("alpha")

        contacts = await store.get_contacts("alpha")
        contact_map = {c["name"]: c["unread_count"] for c in contacts}
        assert contact_map["beta"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_mark_read_only_affects_received(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("beta", "alpha", "to alpha")
        await store.send("alpha", "beta", "to beta")

        await store.mark_read("alpha")

        unread_alpha = await store.unread_count("alpha")
        unread_beta = await store.unread_count("beta")
        assert unread_alpha == 0
        assert unread_beta == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unread_count_empty(tmp_path):
    store = await _store(tmp_path)
    try:
        count = await store.unread_count("lonely")
        assert count == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_unread_count_increments(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("beta", "alpha", "msg1")
        assert await store.unread_count("alpha") == 1

        await store.send("gamma", "alpha", "msg2")
        assert await store.unread_count("alpha") == 2

        await store.send("alpha", "beta", "sent by alpha")
        assert await store.unread_count("alpha") == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_existing_message(tmp_path):
    store = await _store(tmp_path)
    try:
        msg_id = await store.send("alpha", "beta", "to be deleted")
        result = await store.delete(msg_id)
        assert result is True

        msgs = await store.get_messages("alpha")
        assert len(msgs) == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_nonexistent_message(tmp_path):
    store = await _store(tmp_path)
    try:
        result = await store.delete(99999)
        assert result is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_specific_message_preserves_others(tmp_path):
    store = await _store(tmp_path)
    try:
        id1 = await store.send("alpha", "beta", "keep me")
        id2 = await store.send("alpha", "beta", "delete me")
        id3 = await store.send("alpha", "beta", "keep me too")

        await store.delete(id2)

        msgs = await store.get_messages("alpha")
        assert len(msgs) == 2
        remaining_ids = {m["id"] for m in msgs}
        assert remaining_ids == {id1, id3}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_search_all_agents(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "hello world")
        await store.send("gamma", "delta", "goodbye world")
        await store.send("alpha", "beta", "no match here")

        results = await store.search("world")
        assert len(results) == 2
        bodies = {m["message"] for m in results}
        assert "hello world" in bodies
        assert "goodbye world" in bodies
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_search_filtered_by_agent(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "hello world")
        await store.send("gamma", "delta", "hello world too")

        results = await store.search("hello", agent_name="alpha")
        assert len(results) == 1
        assert results[0]["from"] == "alpha"

        results_g = await store.search("hello", agent_name="gamma")
        assert len(results_g) == 1
        assert results_g[0]["from"] == "gamma"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_search_no_results(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "hello")
        results = await store.search("zzzzz")
        assert len(results) == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_search_orders_by_timestamp_desc(tmp_path):
    store = await _store(tmp_path)
    try:
        times = iter([100.0, 200.0, 300.0])
        with patch("tinyagentos.agent_messages.time.time", side_effect=lambda: next(times)):
            await store.send("alpha", "beta", "oldest match")
            await store.send("alpha", "beta", "middle match")
            await store.send("alpha", "beta", "newest match")

        results = await store.search("match")
        assert [m["message"] for m in results] == ["newest match", "middle match", "oldest match"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_search_respects_limit(tmp_path):
    store = await _store(tmp_path)
    try:
        for i in range(5):
            await store.send("alpha", "beta", f"match-{i}")

        results = await store.search("match", limit=3)
        assert len(results) == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_messages_empty_store(tmp_path):
    store = await _store(tmp_path)
    try:
        msgs = await store.get_messages("nobody")
        assert msgs == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_conversation_no_messages(tmp_path):
    store = await _store(tmp_path)
    try:
        convo = await store.get_conversation("x", "y")
        assert convo == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_contacts_no_messages(tmp_path):
    store = await _store(tmp_path)
    try:
        contacts = await store.get_contacts("nobody")
        assert contacts == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_send_returns_incrementing_ids(tmp_path):
    store = await _store(tmp_path)
    try:
        id1 = await store.send("a", "b", "first")
        id2 = await store.send("a", "b", "second")
        id3 = await store.send("a", "b", "third")
        assert id1 < id2 < id3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_message_read_defaults_to_false(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("alpha", "beta", "unread msg")
        msgs = await store.get_messages("beta")
        assert msgs[0]["read"] is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_mark_read_idempotent(tmp_path):
    store = await _store(tmp_path)
    try:
        await store.send("beta", "alpha", "msg")
        await store.mark_read("alpha")
        await store.mark_read("alpha")
        assert await store.unread_count("alpha") == 0
    finally:
        await store.close()
