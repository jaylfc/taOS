"""Tests for the Kilo CLI adapter: stream-json events onto taOS reply kinds."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyagentos.adapters.kilo_cli_adapter import (
    KiloCliAdapter,
    KiloCliConfig,
    map_kilo_event,
)
from tinyagentos.kilo_runtime import KiloHarness

SECRET = "sk-kilo-ADAPTERSECRET"


def _state() -> dict:
    return {"text": ""}


# ---------------------------------------------------------------- codec
def test_text_event_maps_to_delta():
    evt = {"type": "text", "sessionID": "s1", "part": {"type": "text", "text": "pong"}}
    assert map_kilo_event(evt, _state()) == [("delta", {"content": "pong"})]


def test_text_event_without_text_part_is_noop():
    assert map_kilo_event({"type": "text", "part": {}}, _state()) == []
    assert map_kilo_event({"type": "text"}, _state()) == []
    assert map_kilo_event({"type": "step_start", "sessionID": "s1"}, _state()) == []
    assert map_kilo_event({"type": "step_finish", "sessionID": "s1"}, _state()) == []


def test_text_collects_into_state():
    state = _state()
    map_kilo_event({"type": "text", "part": {"text": "a"}}, state)
    map_kilo_event({"type": "text", "part": {"text": "b"}}, state)
    assert state["text"] == "ab"


# ---------------------------------------------------------------- adapter
def _fake(tmp_path, body):
    p = tmp_path / "kilo"
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def _adapter(tmp_path, body, replies):
    h = KiloHarness(home=tmp_path / "home", key=SECRET, model="kilo/kilo-auto/free",
                    binary=_fake(tmp_path, body))
    h.write_home()
    return KiloCliAdapter(KiloCliConfig(harness=h, system="sys"), replies.append)


_OK = ('#!/bin/bash\ntest -n "$KILO_API_KEY" || exit 1\n'
       'echo \'{"type":"step_start","sessionID":"ses-1"}\'\n'
       'echo \'{"type":"text","sessionID":"ses-1","part":{"type":"text","text":"pong"}}\'\n'
       'echo \'{"type":"step_finish","sessionID":"ses-1"}\'\n')


@pytest.mark.asyncio
async def test_prompt_emits_delta_then_final_and_stores_session(tmp_path):
    replies: list[dict] = []
    a = _adapter(tmp_path, _OK, replies)
    await a.ensure_session()
    await a.prompt("hello", trace_id="t1")
    assert [r["kind"] for r in replies] == ["delta", "final"]
    assert all(r["trace_id"] == "t1" for r in replies)
    assert replies[0]["content"] == "pong"
    assert replies[-1]["content"] == "pong"
    assert a.session_id == "ses-1"
    await a.close()


@pytest.mark.asyncio
async def test_prompt_empty_reply_emits_final_with_empty_content(tmp_path):
    """A successful turn with no text events emits a final frame (the route's
    silent-stream guard turns an empty-content final into an error frame)."""
    body = '#!/bin/bash\ntest -n "$KILO_API_KEY" || exit 1\n'
    replies: list[dict] = []
    a = _adapter(tmp_path, body, replies)
    await a.prompt("hi")
    assert [r["kind"] for r in replies] == ["final"]
    assert replies[-1]["content"] == ""


@pytest.mark.asyncio
async def test_attachments_give_one_error(tmp_path):
    replies: list[dict] = []
    a = _adapter(tmp_path, _OK, replies)
    await a.prompt("x", trace_id="t", attachments=[{"url": "u"}])
    assert [r["kind"] for r in replies] == ["error"]
    assert "not supported" in replies[0]["error"]
    assert replies[0]["trace_id"] == "t"


@pytest.mark.asyncio
async def test_failed_turn_emits_error_reply_with_data_message_only(tmp_path):
    body = ('#!/bin/bash\ntest -n "$KILO_API_KEY" || exit 1\n'
            'printf \'%s\\n\' \'{"type":"error","sessionID":"ses-1",'
            '"error":{"name":"APIError","data":{"message":"rate limited",'
            '"statusCode":429,"headers":{"x-ratelimit-limit":"10"}}}}\'\n'
            'exit 1\n')
    replies: list[dict] = []
    a = _adapter(tmp_path, body, replies)
    await a.prompt("x", trace_id="t")
    assert [r["kind"] for r in replies] == ["error"]
    assert "rate limited" in replies[0]["error"]
    assert "statusCode" not in replies[0]["error"]
    assert "headers" not in replies[0]["error"]
    assert "x-ratelimit" not in replies[0]["error"]


@pytest.mark.asyncio
async def test_previous_session_is_resumed(tmp_path):
    body = ('#!/bin/bash\ntest -n "$KILO_API_KEY" || exit 1\n'
            'printf \'%s\\n\' "$@" > "$(dirname "$0")/argv"\n'
            'echo \'{"type":"text","sessionID":"s2","part":{"type":"text","text":"pong"}}\'\n')
    replies: list[dict] = []
    a = _adapter(tmp_path, body, replies)
    a.session_id = "s1"
    await a.prompt("x")
    argv = (tmp_path / "argv").read_text().split("\n")
    assert argv[argv.index("-s") + 1] == "s1"
    assert a.session_id == "s2"


@pytest.mark.asyncio
async def test_first_turn_prefixes_system_prompt(tmp_path):
    body = ('#!/bin/bash\ntest -n "$KILO_API_KEY" || exit 1\n'
            'printf \'%s\\n\' "$@" > "$(dirname "$0")/argv"\n'
            'echo \'{"type":"text","sessionID":"s2","part":{"type":"text","text":"pong"}}\'\n')
    replies: list[dict] = []
    h = KiloHarness(home=tmp_path / "home", key=SECRET, model="kilo/kilo-auto/free",
                    binary=_fake(tmp_path, body))
    h.write_home()
    a = KiloCliAdapter(KiloCliConfig(harness=h, system="sys prompt"), replies.append)
    await a.prompt("hello")
    argv_text = (tmp_path / "argv").read_text()
    assert "sys prompt\n\nhello" in argv_text
    assert a.session_id == "s2"


@pytest.mark.asyncio
async def test_second_turn_sends_user_text_only(tmp_path):
    body = ('#!/bin/bash\ntest -n "$KILO_API_KEY" || exit 1\n'
            'printf \'%s\\n\' "$@" > "$(dirname "$0")/argv"\n'
            'echo \'{"type":"text","sessionID":"s2","part":{"type":"text","text":"pong"}}\'\n')
    replies: list[dict] = []
    h = KiloHarness(home=tmp_path / "home", key=SECRET, model="kilo/kilo-auto/free",
                    binary=_fake(tmp_path, body))
    h.write_home()
    a = KiloCliAdapter(KiloCliConfig(harness=h, system="sys prompt"), replies.append)
    a.session_id = "s1"
    await a.prompt("hello")
    argv_text = (tmp_path / "argv").read_text()
    assert "sys prompt" not in argv_text
    argv = argv_text.split("\n")
    assert argv[argv.index("-s") + 1] == "s1"


@pytest.mark.asyncio
async def test_close_is_a_noop(tmp_path):
    a = _adapter(tmp_path, _OK, [])
    await a.close()
    await a.close()