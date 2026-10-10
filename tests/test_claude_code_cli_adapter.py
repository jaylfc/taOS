"""Tests for the Claude Code CLI adapter: stream-json events onto taOS reply kinds."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyagentos.adapters.claude_code_cli_adapter import (
    ClaudeCodeCliAdapter,
    ClaudeCodeCliConfig,
    map_claude_event,
)
from tinyagentos.claude_code_runtime import ClaudeCodeHarness

FIXTURE = Path(__file__).parent / "fixtures" / "claude_code_stream.jsonl"
TOKEN = "sk-ant-oat01-ADAPTERSECRET"


def _state() -> dict:
    return {"text": ""}


def test_recorded_stream_maps_to_one_delta_and_one_final():
    state = _state()
    out = []
    for line in FIXTURE.read_text().splitlines():
        out.extend(map_claude_event(json.loads(line), state))
    kinds = [k for k, _ in out]
    assert kinds == ["delta", "final"]
    assert out[0][1] == {"content": "ok"}
    assert out[1][1] == {"content": "ok"}


def test_fixture_is_scrubbed():
    text = FIXTURE.read_text()
    assert "/home" not in text
    assert "hook_started" not in text and "memory_paths" not in text


def test_tool_use_then_tool_result():
    state = _state()
    use = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}]}}
    res = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "a.txt"}]}}
    assert map_claude_event(use, state) == [
        ("tool_call", {"name": "Bash", "input": {"command": "ls"}, "id": "tu1"})]
    assert map_claude_event(res, state) == [
        ("tool_result", {"tool_use_id": "tu1", "content": "a.txt"})]


def test_tool_result_list_content_is_joined():
    res = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t", "content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]}}
    [(kind, payload)] = map_claude_event(res, _state())
    assert kind == "tool_result"
    assert payload["content"] == "ab"


def test_error_result():
    evt = {"type": "result", "is_error": True, "result": "rate limited"}
    assert map_claude_event(evt, _state()) == [("error", {"error": "rate limited"})]
    evt2 = {"type": "result", "is_error": True}
    assert map_claude_event(evt2, _state()) == [("error", {"error": "claude code turn failed"})]


def test_final_falls_back_to_collected_text():
    state = {"text": "streamed"}
    assert map_claude_event({"type": "result", "is_error": False, "result": ""}, state) == [
        ("final", {"content": "streamed"})]


@pytest.mark.parametrize("evt", [
    {"type": "system", "subtype": "init"},
    {"type": "rate_limit_event"},
    {"type": "something_new"},
])
def test_ignored_events(evt):
    assert map_claude_event(evt, _state()) == []


# ---------------------------------------------------------------- adapter
def _fake(tmp_path, body):
    p = tmp_path / "claude"
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def _adapter(tmp_path, body, replies):
    h = ClaudeCodeHarness(home=tmp_path / "home", token=TOKEN, model="m", binary=_fake(tmp_path, body))
    h.write_home()
    return ClaudeCodeCliAdapter(ClaudeCodeCliConfig(harness=h, system="sys"), replies.append)


_OK = ('#!/bin/bash\ncat >/dev/null\n'
       'echo \'{"type":"system","session_id":"s9"}\'\n'
       'echo \'{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\'\n'
       'echo \'{"type":"result","is_error":false,"result":"hi","session_id":"s9"}\'\n')


@pytest.mark.asyncio
async def test_prompt_emits_delta_then_final_and_stores_session(tmp_path):
    replies: list[dict] = []
    a = _adapter(tmp_path, _OK, replies)
    await a.ensure_session()
    await a.prompt("hello", trace_id="t1")
    assert [r["kind"] for r in replies] == ["delta", "final"]
    assert all(r["trace_id"] == "t1" for r in replies)
    assert replies[-1]["content"] == "hi"
    assert a.session_id == "s9"
    await a.close()


@pytest.mark.asyncio
async def test_attachments_give_one_error(tmp_path):
    replies: list[dict] = []
    a = _adapter(tmp_path, _OK, replies)
    await a.prompt("x", trace_id="t", attachments=[{"url": "u"}])
    assert [r["kind"] for r in replies] == ["error"]
    assert "not supported" in replies[0]["error"]


@pytest.mark.asyncio
async def test_failed_turn_is_an_error_with_redacted_detail(tmp_path):
    body = f'#!/bin/bash\ncat >/dev/null\necho "bad {TOKEN}" >&2\nexit 3\n'
    replies: list[dict] = []
    a = _adapter(tmp_path, body, replies)
    await a.prompt("x", trace_id="t")
    assert [r["kind"] for r in replies] == ["error"]
    assert TOKEN not in json.dumps(replies)
    assert "[redacted]" in replies[0]["error"]


@pytest.mark.asyncio
async def test_error_result_event_is_one_error_reply(tmp_path):
    body = ('#!/bin/bash\ncat >/dev/null\n'
            'echo \'{"type":"result","is_error":true,"result":"nope"}\'\n')
    replies: list[dict] = []
    a = _adapter(tmp_path, body, replies)
    await a.prompt("x")
    assert [r["kind"] for r in replies] == ["error"]
    assert "nope" in replies[0]["error"]


@pytest.mark.asyncio
async def test_previous_session_is_resumed(tmp_path):
    body = ('#!/bin/bash\ncat >/dev/null\nprintf "%s\\n" "$@" > "$(dirname "$0")/argv"\n'
            'echo \'{"type":"result","is_error":false,"result":"r","session_id":"s2"}\'\n')
    replies: list[dict] = []
    a = _adapter(tmp_path, body, replies)
    a.session_id = "s1"
    await a.prompt("x")
    argv = (tmp_path / "argv").read_text().split("\n")
    assert argv[argv.index("--resume") + 1] == "s1"
    assert a.session_id == "s2"
