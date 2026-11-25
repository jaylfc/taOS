"""Agent memory recall through the real ``taosmd.answer_context``.

The store is the one a real taosmd 0.4.0 wrote (committed fixture); the call is
the real ``answer_context`` and the real ``default_budget_tokens``. Nothing in
taosmd is faked: failures come from real invalid input (a bad agent name, a data
dir that is a file).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
import taosmd

from tinyagentos.memory_recall import ASSUMED_WINDOW_TOKENS, recall

REPO = Path(__file__).resolve().parent.parent
FIXTURE_040 = REPO / "tests" / "fixtures" / "taosmd_040_store"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    dest = tmp_path / "data"
    shutil.copytree(FIXTURE_040, dest, ignore=shutil.ignore_patterns("make_fixture.py"))
    return dest


@pytest.mark.asyncio
async def test_recall_returns_the_dated_context_from_a_040_store(store):
    r = await recall("hello from 0.4.0", agent="tester", data_dir=store, window_tokens=32768, used_tokens=22000)
    assert r.status == "ok" and r.usable
    assert r.context.startswith("Current date:") and "hello" in r.context
    assert r.budget_tokens == taosmd.default_budget_tokens(32768, 22000) == 10256
    assert r.hits_total >= 1 and r.sessions_included == 1 and r.tokens_used > 0
    assert r.agent_note() == ""


@pytest.mark.asyncio
async def test_reasoning_reserve_comes_off_the_budget(store):
    plain = await recall("hello from 0.4.0", agent="tester", data_dir=store, window_tokens=8192, used_tokens=2000)
    reasoning = await recall(
        "hello from 0.4.0", agent="tester", data_dir=store, window_tokens=8192, used_tokens=2000, reasoning_reserve=3000,
    )
    assert reasoning.budget_tokens == taosmd.default_budget_tokens(8192, 2000, reasoning_reserve=3000)
    assert reasoning.budget_tokens < plain.budget_tokens


@pytest.mark.asyncio
async def test_nothing_matching_is_no_hits_not_an_error(store, caplog):
    with caplog.at_level(logging.INFO):
        r = await recall("zzzqqq unrelated", agent="tester", data_dir=store, window_tokens=32768, used_tokens=1000)
    assert r.status == "no_hits" and r.context == ""
    assert r.detail
    assert r.agent_note() == ""  # normal: the agent is not told memory is broken


@pytest.mark.asyncio
async def test_matches_that_do_not_fit_are_said_not_silently_dropped(store, caplog):
    with caplog.at_level(logging.WARNING):
        r = await recall("hello from 0.4.0", agent="tester", data_dir=store, window_tokens=520, used_tokens=0)
    assert r.status == "over_budget" and r.context == ""
    assert r.hits_total >= 1 and r.sessions_dropped >= 1
    assert "budget" in r.detail and r.agent_note().startswith("[memory unavailable")
    assert any("budget" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_a_window_with_no_room_is_reported(store):
    r = await recall("hello", agent="tester", data_dir=store, window_tokens=4000, used_tokens=5000)
    assert r.status == "no_budget" and r.agent_note()


@pytest.mark.asyncio
async def test_unknown_window_is_assumed_and_logged(store, caplog):
    with caplog.at_level(logging.INFO):
        r = await recall("hello from 0.4.0", agent="tester", data_dir=store, window_tokens=None, used_tokens=1000)
    assert r.window_assumed is True and r.window_tokens == ASSUMED_WINDOW_TOKENS
    assert any("unknown" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_real_taosmd_failure_is_logged_returned_and_never_raised(store, caplog):
    with caplog.at_level(logging.WARNING):
        r = await recall("hello", agent="Bad Agent!", data_dir=store, window_tokens=32768, used_tokens=1000)
    assert r.status == "error" and r.context == ""
    assert "InvalidAgentNameError" in r.detail
    assert r.agent_note().startswith("[memory unavailable")
    assert any(rec.levelno >= logging.WARNING and "failed" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_unusable_data_dir_is_an_error_not_an_empty_context(tmp_path):
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    r = await recall("hello", agent="tester", data_dir=not_a_dir, window_tokens=32768, used_tokens=1000)
    assert r.status == "error" and "FileExistsError" in r.detail or "NotADirectory" in r.detail


@pytest.mark.asyncio
async def test_nothing_to_ask_is_skipped(store):
    assert (await recall("   ", agent="tester", data_dir=store, window_tokens=32768, used_tokens=0)).status == "skipped"


@pytest.mark.asyncio
async def test_recall_finds_what_taos_itself_writes(tmp_path):
    """Write through taOS's own archive writer, with the event shapes taOS records, then recall."""
    from tinyagentos.archive_writer import open_archive_writer, release_archive_writer

    data = tmp_path / "data"
    data.mkdir()
    writer = open_archive_writer("test", data / "archive", index_path=data / "archive-index.db")
    await writer.init()
    try:
        # routes/chat.py: an agent-authored chat message
        await writer.record(
            "conversation",
            {"content": "the staging password rotation happens every thursday", "channel_id": "c1",
             "message_id": "m1", "author_id": "tester", "author_type": "agent"},
            agent_name="tester", summary="the staging password rotation happens every thursday",
        )
        # bridge_session.py: an agent's final reply
        await writer.record(
            "final",
            {"content": "the backup bucket is called nightly-vault", "trace_id": "t1", "reply_id": "r1", "channel_id": "c1"},
            agent_name="tester", summary="the backup bucket is called nightly-vault",
        )
    finally:
        await release_archive_writer(writer)

    conv = await recall("password rotation thursday", agent="tester", data_dir=data, window_tokens=32768, used_tokens=1000)
    final = await recall("backup bucket called", agent="tester", data_dir=data, window_tokens=32768, used_tokens=1000)
    other = await recall("password rotation thursday", agent="someone-else", data_dir=data, window_tokens=32768, used_tokens=1000)
    assert conv.status == "ok" and "thursday" in conv.context, conv
    assert final.status == "ok" and "nightly-vault" in final.context, final
    assert "thursday" not in other.context  # another agent's memory stays its own
