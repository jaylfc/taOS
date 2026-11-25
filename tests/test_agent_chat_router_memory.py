"""The chat router hands each recipient what taOSmd recalls for it, in a fixed order.

Context sent to the bridge: the system manual first, then the recalled memory,
then the channel (or thread) history. Real taosmd answer_context over a store a
real taosmd 0.4.0 wrote; only the chat stores and the bridge are stand-ins.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.agent_chat_router import AgentChatRouter
from tinyagentos.chat.group_policy import GroupPolicy

REPO = Path(__file__).resolve().parent.parent
FIXTURE_040 = REPO / "tests" / "fixtures" / "taosmd_040_store"


class _Bridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def enqueue_user_message(self, slug, msg):
        self.calls.append((slug, msg))


def _state(tmp_path: Path, agents: list[dict], history=None, thread_history=None):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE_040, data, ignore=shutil.ignore_patterns("make_fixture.py"))
    state = MagicMock()
    state.data_dir = data
    state.config = MagicMock()
    state.config.agents = agents
    state.chat_messages = MagicMock()
    state.chat_messages.send_message = AsyncMock(return_value={
        "id": "x", "channel_id": "c1", "author_id": "a", "author_type": "agent", "content": "", "created_at": 1.0,
    })
    state.chat_messages.get_messages = AsyncMock(return_value=history or [])
    state.chat_messages.get_thread_messages = AsyncMock(return_value=thread_history or [])
    state.chat_messages.get_message = AsyncMock(return_value={
        "id": "p1", "author_id": "tester", "author_type": "agent", "content": "the parent post",
    })
    state.chat_channels = MagicMock()
    state.chat_channels.update_last_message_at = AsyncMock()
    state.chat_hub = MagicMock()
    state.chat_hub.broadcast = AsyncMock()
    state.chat_hub.next_seq = MagicMock(return_value=1)
    state.bridge_sessions = _Bridge()
    state.group_policy = GroupPolicy()
    state.trace_registry = None
    return state


def _dm(members):
    return {"id": "c1", "type": "dm", "members": members}


def _msg(text, **extra):
    return {"id": "m1", "channel_id": "c1", "author_id": "user", "author_type": "user",
            "content": text, "metadata": {"hops_since_user": 0}, **extra}


def _hist(*pairs):
    return [{"author_id": a, "author_type": t, "content": c, "created_at": float(i)} for i, (a, t, c) in enumerate(pairs)]


@pytest.mark.asyncio
async def test_order_is_manual_then_memory_then_history(tmp_path):
    history = _hist(("user", "user", "earlier question"), ("tester", "agent", "earlier answer"))
    state = _state(tmp_path, [{"name": "tester", "status": "running"}], history=history)
    await AgentChatRouter(state)._route(_msg("hello from 0.4.0"), _dm(["user", "tester"]))
    slug, sent = state.bridge_sessions.calls[0]
    ctx = sent["context"]
    assert slug == "tester" and sent["from"] == "user"
    assert ctx[0]["role"] == "system" and "@-mention routing" in ctx[0]["content"]  # the manual
    assert ctx[1]["role"] == "system" and ctx[1]["content"].startswith("Current date:") and "hello" in ctx[1]["content"]
    assert [m["content"] for m in ctx[2:]] == ["earlier question", "earlier answer"]


@pytest.mark.asyncio
async def test_thread_history_and_scoping_are_kept(tmp_path):
    thread = _hist(("user", "user", "in the thread"))
    state = _state(tmp_path, [{"name": "tester", "status": "running"}], thread_history=thread)
    await AgentChatRouter(state)._route(_msg("hello from 0.4.0", thread_id="p1"), _dm(["user", "tester"]))
    _, sent = state.bridge_sessions.calls[0]
    assert sent["thread_id"] == "p1"
    state.chat_messages.get_thread_messages.assert_awaited()
    contents = [m["content"] for m in sent["context"][2:]]
    assert contents == ["the parent post", "in the thread"]  # parent first, then the thread, not the channel


@pytest.mark.asyncio
async def test_memory_error_is_logged_noted_and_history_is_untouched(tmp_path, caplog):
    history = _hist(("user", "user", "keep me"))
    state = _state(tmp_path, [{"name": "tester", "status": "running"}], history=history)
    state.data_dir = tmp_path / "not-a-dir"
    state.data_dir.write_text("x")
    with caplog.at_level(logging.WARNING):
        await AgentChatRouter(state)._route(_msg("hello"), _dm(["user", "tester"]))
    _, sent = state.bridge_sessions.calls[0]
    ctx = sent["context"]
    assert ctx[1]["role"] == "system" and ctx[1]["content"].startswith("[memory unavailable")
    assert [m["content"] for m in ctx[2:]] == ["keep me"]  # history survived the memory failure
    assert any("memory recall" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_one_recipients_failure_does_not_touch_the_other(tmp_path):
    history = _hist(("user", "user", "shared history"))
    agents = [{"name": "tester", "status": "running"}, {"name": "Bad Name", "status": "running"}]
    state = _state(tmp_path, agents, history=history)
    ch = {"id": "c1", "type": "group", "members": ["user", "tester", "Bad Name"],
          "settings": {"response_mode": "lively", "max_hops": 3, "cooldown_seconds": 0, "rate_cap_per_minute": 20, "muted": []}}
    await AgentChatRouter(state)._route(_msg("hello from 0.4.0"), ch)
    by = {slug: sent["context"] for slug, sent in state.bridge_sessions.calls}
    assert by["tester"][1]["content"].startswith("Current date:")
    assert by["Bad Name"][1]["content"].startswith("[memory unavailable")
    assert [m["content"] for m in by["tester"][2:]] == [m["content"] for m in by["Bad Name"][2:]] == ["shared history"]


@pytest.mark.asyncio
async def test_nothing_relevant_adds_no_note(tmp_path):
    state = _state(tmp_path, [{"name": "tester", "status": "running"}])
    await AgentChatRouter(state)._route(_msg("zzzqqq unrelated"), _dm(["user", "tester"]))
    _, sent = state.bridge_sessions.calls[0]
    assert len(sent["context"]) == 1  # only the manual
