"""Tests for the OMP adapter against a scripted mock ACP server.

The mock speaks the real ACP wire format (JSON-RPC 2.0 over a stdio pipe pair)
and emits a scripted turn: text deltas, a thought, a plan, a tool_call +
tool_call_update, a permission request, then PromptResponse{stopReason}. The
tests assert the handshake works and that every session/update variant maps to
the taOS reply kind that bridge_session.record_reply consumes.

No real omp binary is required; the adapter defaults to command=["omp", "acp"]
but the tests inject the stdio pair directly via start().
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tinyagentos.adapters.acp_adapter import ACPConfig
from tinyagentos.adapters.omp_adapter import OMPAdapter, OMPConfig


class MockACPServer:
    """In-process ACP server: reads JSON-RPC lines, scripts a prompt turn.

    Wired to the adapter via a pair of asyncio pipes so no subprocess or model
    is needed. Emits the full session/update variant set during a turn.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._r = reader
        self._w = writer
        self.session_id = "sess_mock_1"
        self.initialized = False
        self.permission_outcome: dict | None = None
        self._perm_event = asyncio.Event()

    async def _send(self, obj: dict) -> None:
        self._w.write((json.dumps(obj) + "\n").encode())
        await self._w.drain()

    async def run(self) -> None:
        while True:
            line = await self._r.readline()
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            await self._handle(msg)

    async def _handle(self, msg: dict) -> None:
        method = msg.get("method")
        rid = msg.get("id")
        if method is None and "result" in msg:
            self.permission_outcome = msg["result"]
            self._perm_event.set()
            return
        if method == "initialize":
            self.initialized = True
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {"promptCapabilities": {"image": False}},
                    },
                }
            )
        elif method == "session/new":
            await self._send(
                {"jsonrpc": "2.0", "id": rid, "result": {"sessionId": self.session_id}}
            )
        elif method == "session/prompt":
            asyncio.create_task(self._run_turn(rid, msg["params"]["sessionId"]))

    async def _update(self, session_id: str, update: dict) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": session_id, "update": update},
            }
        )

    async def _run_turn(self, rid, session_id: str) -> None:
        await self._update(
            session_id,
            {
                "sessionUpdate": "plan",
                "entries": [
                    {"content": "Read the file", "status": "pending", "priority": "high"},
                    {"content": "Summarise it", "status": "pending", "priority": "medium"},
                ],
            },
        )
        await self._update(
            session_id,
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "I should read config.json first."},
            },
        )
        await self._update(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Let me "}},
        )
        await self._update(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "check that."}},
        )
        await self._update(
            session_id,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "call_1",
                "title": "Read config.json",
                "kind": "read",
                "status": "pending",
                "rawInput": {"path": "config.json"},
            },
        )
        perm_id = 9001
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": perm_id,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {"toolCallId": "call_1", "title": "Read config.json", "kind": "read"},
                    "options": [
                        {"optionId": "o-allow", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "o-reject", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            }
        )
        await asyncio.wait_for(self._perm_event.wait(), timeout=5)
        await self._update(
            session_id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call_1",
                "status": "completed",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "{\"k\":1}"}}
                ],
                "rawOutput": {"k": 1},
            },
        )
        await self._update(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " Done."}},
        )
        await self._send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


class _MemoryPipe:
    """A bidirectional in-memory stream pair (no OS sockets/subprocess)."""

    def __init__(self):
        self.a_to_b = asyncio.StreamReader()
        self.b_to_a = asyncio.StreamReader()
        self.a_writer = _QueueWriter(self.a_to_b)
        self.b_writer = _QueueWriter(self.b_to_a)

    def client_io(self):
        return self.a_writer, self.b_to_a

    def server_io(self):
        return self.b_to_a, self.a_to_b


class _QueueWriter:
    """Minimal StreamWriter-like shim feeding bytes into a StreamReader."""

    def __init__(self, target: asyncio.StreamReader):
        self._target = target

    def write(self, data: bytes) -> None:
        self._target.feed_data(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._target.feed_eof()


@pytest.fixture
def collected():
    return []


@pytest.fixture
def sink(collected):
    def _sink(reply: dict):
        collected.append(reply)

    return _sink


@pytest.mark.asyncio
async def test_omp_defaults_to_acp_command():
    """OMPAdapter defaults to command=['omp', 'acp'] when no config is given."""
    adapter = OMPAdapter(sink=lambda r: None)
    assert adapter._effective_command() == ["omp", "acp"]


def test_omp_forces_omp_command_when_config_given():
    """OMPAdapter forces command=['omp', 'acp'] when handed a non-OMP ACPConfig."""
    adapter = OMPAdapter(ACPConfig(command=["openclaw", "acp"]), sink=lambda r: None)
    assert adapter._effective_command() == ["omp", "acp"]


def test_omp_config_is_importable_and_usable():
    """OMPConfig is importable and usable with only session_key supplied."""
    cfg = OMPConfig(session_key="agent:main:main")
    assert cfg.command == ["omp", "acp"]
    assert cfg.session_key == "agent:main:main"


@pytest.mark.asyncio
async def test_omp_adapter_full_turn(sink, collected):
    pipe = _MemoryPipe()
    client_writer = pipe.a_writer
    client_reader = pipe.b_to_a
    server_reader = pipe.a_to_b
    server_writer = pipe.b_writer

    server = MockACPServer(server_reader, server_writer)
    server_task = asyncio.create_task(server.run())

    cfg = ACPConfig(command=["omp", "acp"], permission_policy="allow_once", request_timeout=5)
    adapter = OMPAdapter(cfg, sink)
    await adapter.start(client_writer, client_reader)

    init = await adapter.initialize()
    assert server.initialized is True
    assert init["protocolVersion"] == 1

    sid = await adapter.new_session()
    assert sid == "sess_mock_1"

    stop = await adapter.prompt(sid, "summarise config.json", trace_id="t-abc")
    assert stop == "end_turn"

    await asyncio.sleep(0.05)
    await adapter.close()
    server_task.cancel()

    kinds = [c["kind"] for c in collected]
    assert "reasoning" in kinds
    assert "delta" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "final" in kinds

    deltas = "".join(c["content"] for c in collected if c["kind"] == "delta")
    assert deltas == "Let me check that. Done."

    finals = [c for c in collected if c["kind"] == "final"]
    assert len(finals) == 1
    assert finals[0]["content"] == "Let me check that. Done."

    tcs = [c for c in collected if c["kind"] == "tool_call" and not c["tool"].startswith("permission:")]
    assert len(tcs) == 1
    assert tcs[0]["tool"] == "Read config.json"
    assert tcs[0]["args"] == {"path": "config.json"}

    trs = [c for c in collected if c["kind"] == "tool_result"]
    assert len(trs) == 1
    assert trs[0]["tool"] == "Read config.json"
    assert trs[0]["success"] is True
    assert trs[0]["result"] == {"k": 1}

    assert all(c["trace_id"] == "t-abc" for c in collected)


@pytest.mark.asyncio
async def test_omp_adapter_refusal_stop_reason(collected):
    def sink(reply):
        collected.append(reply)

    pipe = _MemoryPipe()

    class RefusingServer(MockACPServer):
        async def _run_turn(self, rid, session_id):
            await self._update(
                session_id,
                {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "No."}},
            )
            await self._send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "refusal"}})

    server = RefusingServer(pipe.a_to_b, pipe.b_writer)
    server_task = asyncio.create_task(server.run())

    adapter = OMPAdapter(ACPConfig(command=["omp", "acp"], request_timeout=5), sink)
    await adapter.start(pipe.a_writer, pipe.b_to_a)
    await adapter.initialize()
    sid = await adapter.new_session()
    stop = await adapter.prompt(sid, "do something bad", trace_id="t-3")
    await asyncio.sleep(0.05)
    await adapter.close()
    server_task.cancel()

    assert stop == "refusal"
    kinds = [c["kind"] for c in collected]
    assert "error" in kinds
    err = next(c for c in collected if c["kind"] == "error")
    assert "refusal" in err["error"]
