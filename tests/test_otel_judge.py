"""Phase 4 reasoning judge tests.

Covers:
  - Trivial run (no tool_call) is skipped — no reasoning_audit written
  - Trivial run (no llm_call) is skipped
  - Non-trivial run calls the model and writes reasoning_audit on pass/warn/fail
  - Malformed JSON response from model falls back to verdict=warn + flags note
  - Markdown-fenced JSON is unwrapped before parsing
  - session_end lifecycle event fires judge.schedule() via trace_store
  - judge.schedule() is a no-op when not in an async context
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tinyagentos.otel.judge import ReasoningJudge, _format_trace, _resolve_judge_model
from tinyagentos.trace_store import AgentTraceStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path: Path, slug: str = "test-agent") -> AgentTraceStore:
    return AgentTraceStore(tmp_path, slug)


def _mock_llm_response(verdict: str = "pass", flags: list[str] | None = None) -> MagicMock:
    """Build a mock httpx response returning a clean JSON verdict."""
    body = json.dumps({"verdict": verdict, "flags": flags or []})
    content = f'{{"choices": [{{"message": {{"content": {json.dumps(body)}}}}}]}}'
    resp = MagicMock()
    resp.json.return_value = json.loads(content)
    resp.raise_for_status = MagicMock()
    return resp


def _events(with_llm: bool = True, with_tool: bool = True) -> list[dict]:
    base: list[dict] = []
    if with_llm:
        base.append({
            "kind": "llm_call",
            "model": "claude-sonnet-4-6",
            "tokens_in": 100,
            "tokens_out": 50,
            "payload": {"status": "success"},
        })
    if with_tool:
        base.append({
            "kind": "tool_call",
            "payload": {"tool": "read_file", "args": {"path": "/tmp/x"}},
        })
        base.append({
            "kind": "tool_result",
            "payload": {"tool": "read_file", "success": True, "result": "hello"},
        })
    return base


# ---------------------------------------------------------------------------
# _format_trace
# ---------------------------------------------------------------------------

def test_format_trace_empty():
    result = _format_trace([])
    assert result == "(empty trace)"


def test_format_trace_llm_call():
    events = [{"kind": "llm_call", "model": "gpt-4o", "tokens_in": 10, "tokens_out": 5, "payload": {"status": "success"}}]
    result = _format_trace(events)
    assert "[llm_call]" in result
    assert "gpt-4o" in result
    assert "tokens=15" in result


def test_format_trace_tool_call_args_truncated():
    long_args = {"key": "x" * 200}
    events = [{"kind": "tool_call", "payload": {"tool": "do_thing", "args": long_args}}]
    result = _format_trace(events)
    assert "[tool_call]" in result
    assert "do_thing" in result
    # args are truncated at 120 chars in the formatting
    assert len(result) < 500


def test_format_trace_skips_meta_events():
    events = [
        {"kind": "reasoning_audit", "payload": {"verdict": "pass"}},
        {"kind": "lifecycle", "payload": {"event": "session_end"}},
        {"kind": "message_in", "payload": {"text": "hi"}},
    ]
    result = _format_trace(events)
    assert result == "(empty trace)"


# ---------------------------------------------------------------------------
# _resolve_judge_model
# ---------------------------------------------------------------------------

def test_resolve_judge_model_explicit_override():
    assert _resolve_judge_model("my/model") == "my/model"


def test_resolve_judge_model_fallback():
    with patch("tinyagentos.otel.judge.taosmd", create=True, get_memory_model=lambda: None):
        result = _resolve_judge_model(None)
        assert result == "kilo-auto/free"


def test_resolve_judge_model_from_taosmd():
    with patch.dict("sys.modules", {"taosmd": MagicMock(get_memory_model=lambda: "local/gemma-3")}):
        result = _resolve_judge_model(None)
        assert result == "local/gemma-3"


# ---------------------------------------------------------------------------
# ReasoningJudge._run_inner — trivial run skips
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_judge_skips_trivial_no_tool(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge()

    store_list = AsyncMock(return_value=_events(with_llm=True, with_tool=False))
    with patch.object(store, "list", store_list):
        record_mock = AsyncMock()
        with patch.object(store, "record", record_mock):
            await judge._run_inner(store, "trace-1")

    record_mock.assert_not_called()


@pytest.mark.asyncio
async def test_judge_skips_trivial_no_llm(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge()

    store_list = AsyncMock(return_value=_events(with_llm=False, with_tool=True))
    with patch.object(store, "list", store_list):
        record_mock = AsyncMock()
        with patch.object(store, "record", record_mock):
            await judge._run_inner(store, "trace-1")

    record_mock.assert_not_called()


# ---------------------------------------------------------------------------
# ReasoningJudge._run_inner — non-trivial run calls model + stores verdict
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_judge_pass_verdict(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge(judge_model="test-model")

    mock_resp = _mock_llm_response("pass", [])
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.object(store, "list", AsyncMock(return_value=_events())), \
         patch.object(store, "record", AsyncMock()) as record_mock, \
         patch("tinyagentos.otel.judge.httpx.AsyncClient", return_value=mock_client):
        await judge._run_inner(store, "trace-pass")

    record_mock.assert_awaited_once()
    call_kwargs = record_mock.call_args
    assert call_kwargs[0][0] == "reasoning_audit"
    payload = call_kwargs[1]["payload"]
    assert payload["verdict"] == "pass"
    assert payload["flags"] == []
    assert payload["model"] == "test-model"
    assert isinstance(payload["latency_ms"], int)


@pytest.mark.asyncio
async def test_judge_fail_verdict_with_flags(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge(judge_model="test-model")

    flags = ["tool result ignored", "conclusion contradicts evidence"]
    mock_resp = _mock_llm_response("fail", flags)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.object(store, "list", AsyncMock(return_value=_events())), \
         patch.object(store, "record", AsyncMock()) as record_mock, \
         patch("tinyagentos.otel.judge.httpx.AsyncClient", return_value=mock_client):
        await judge._run_inner(store, "trace-fail")

    payload = record_mock.call_args[1]["payload"]
    assert payload["verdict"] == "fail"
    assert payload["flags"] == flags


@pytest.mark.asyncio
async def test_judge_unwraps_markdown_fences(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge(judge_model="test-model")

    fenced_json = '```json\n{"verdict": "warn", "flags": ["slight drift"]}\n```'
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": fenced_json}}]}
    resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=resp)

    with patch.object(store, "list", AsyncMock(return_value=_events())), \
         patch.object(store, "record", AsyncMock()) as record_mock, \
         patch("tinyagentos.otel.judge.httpx.AsyncClient", return_value=mock_client):
        await judge._run_inner(store, "trace-fenced")

    payload = record_mock.call_args[1]["payload"]
    assert payload["verdict"] == "warn"


@pytest.mark.asyncio
async def test_judge_unknown_verdict_becomes_warn(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge(judge_model="test-model")

    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": '{"verdict": "maybe", "flags": []}'}}]}
    resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=resp)

    with patch.object(store, "list", AsyncMock(return_value=_events())), \
         patch.object(store, "record", AsyncMock()) as record_mock, \
         patch("tinyagentos.otel.judge.httpx.AsyncClient", return_value=mock_client):
        await judge._run_inner(store, "trace-bad-verdict")

    payload = record_mock.call_args[1]["payload"]
    assert payload["verdict"] == "warn"
    assert any("maybe" in f for f in payload["flags"])


@pytest.mark.asyncio
async def test_judge_http_error_is_swallowed(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge(judge_model="test-model")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("network error"))

    with patch.object(store, "list", AsyncMock(return_value=_events())), \
         patch.object(store, "record", AsyncMock()) as record_mock, \
         patch("tinyagentos.otel.judge.httpx.AsyncClient", return_value=mock_client):
        await judge._run(store, "trace-err")  # _run wraps _run_inner

    record_mock.assert_not_called()


# ---------------------------------------------------------------------------
# schedule() — no-op outside async context
# ---------------------------------------------------------------------------

def test_judge_schedule_noop_outside_asyncio(tmp_path):
    store = _store(tmp_path)
    judge = ReasoningJudge()
    # Should not raise even outside a running event loop.
    judge.schedule(store, "trace-xyz")
    assert len(judge._pending_tasks) == 0


# ---------------------------------------------------------------------------
# trace_store integration — session_end fires judge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trace_store_fires_judge_on_session_end(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-a")
    judge = MagicMock()
    judge.schedule = MagicMock()
    store.set_judge(judge)

    await store.record(
        "lifecycle",
        trace_id="trace-ses",
        payload={"event": "session_end"},
    )

    judge.schedule.assert_called_once_with(store, "trace-ses")


@pytest.mark.asyncio
async def test_trace_store_does_not_fire_judge_on_other_lifecycle(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-b")
    judge = MagicMock()
    judge.schedule = MagicMock()
    store.set_judge(judge)

    await store.record(
        "lifecycle",
        trace_id="trace-start",
        payload={"event": "session_start"},
    )

    judge.schedule.assert_not_called()


@pytest.mark.asyncio
async def test_trace_store_does_not_fire_judge_without_trace_id(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-c")
    judge = MagicMock()
    judge.schedule = MagicMock()
    store.set_judge(judge)

    await store.record(
        "lifecycle",
        payload={"event": "session_end"},
        # no trace_id
    )

    judge.schedule.assert_not_called()


@pytest.mark.asyncio
async def test_trace_store_no_judge_is_noop(tmp_path):
    store = AgentTraceStore(tmp_path, "agent-d")
    # No judge set — should not raise.
    await store.record(
        "lifecycle",
        trace_id="trace-abc",
        payload={"event": "session_end"},
    )
