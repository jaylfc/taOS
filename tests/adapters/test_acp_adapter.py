"""Tests for the ACP adapter against a scripted mock ACP server.

The mock speaks the real ACP wire format (JSON-RPC 2.0 over a stdio pipe pair)
and emits a scripted turn: text deltas, a thought, a plan, a tool_call +
tool_call_update, a permission request, then PromptResponse{stopReason}. The
tests assert the handshake works and that every session/update variant maps to
the taOS reply ``kind`` that bridge_session.record_reply consumes.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tinyagentos.adapters.acp_adapter import ACPAdapter, ACPConfig, ACPProtocolError


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
        # Captured permission response from the client.
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
        # Responses from the client to our request (permission).
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
            # Run the turn concurrently so run() keeps reading the pipe and can
            # receive the client's permission response mid-turn (otherwise the
            # server would deadlock waiting on _perm_event while blocking reads).
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
        # 1. Plan
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
        # 2. Thought (reasoning)
        await self._update(
            session_id,
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "I should read config.json first."},
            },
        )
        # 3. Streamed assistant text (two deltas)
        await self._update(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Let me "}},
        )
        await self._update(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "check that."}},
        )
        # 4. Tool call (pending) with rawInput args
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
        # 5. Permission request (JSON-RPC request — expects a response)
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
        # Wait for the client's permission answer before continuing the turn.
        await asyncio.wait_for(self._perm_event.wait(), timeout=5)
        # 6. Tool call completed with output
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
        # 7. Final assistant text
        await self._update(
            session_id,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " Done."}},
        )
        # 8. PromptResponse
        await self._send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


def _make_pipe():
    """Create a connected (reader, writer) pair over an in-memory transport."""
    # Two queues simulate two directions. We build StreamReader/Writer on top of
    # a simple in-memory protocol so adapter and mock can talk without a socket.
    return _MemoryPipe()


class _MemoryPipe:
    """A bidirectional in-memory stream pair (no OS sockets/subprocess)."""

    def __init__(self):
        self.a_to_b = asyncio.StreamReader()
        self.b_to_a = asyncio.StreamReader()
        self.a_writer = _QueueWriter(self.a_to_b)
        self.b_writer = _QueueWriter(self.b_to_a)

    # Adapter uses (stdin=writer to server, stdout=reader from server)
    def client_io(self):
        return self.a_writer, self.b_to_a  # client writes to a_to_b, reads b_to_a

    def server_io(self):
        return self.b_to_a, self.a_to_b  # unused directly; server uses readers below


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
async def test_handshake_and_full_turn_mapping(sink, collected):
    pipe = _MemoryPipe()
    # Adapter: writes to a_to_b, reads from b_to_a.
    client_writer = pipe.a_writer
    client_reader = pipe.b_to_a
    # Server: reads a_to_b, writes b_to_a.
    server_reader = pipe.a_to_b
    server_writer = pipe.b_writer

    server = MockACPServer(server_reader, server_writer)
    server_task = asyncio.create_task(server.run())

    cfg = ACPConfig(command=["mock"], permission_policy="allow_once", request_timeout=5)
    adapter = ACPAdapter(cfg, sink)
    await adapter.start(client_writer, client_reader)

    init = await adapter.initialize()
    assert server.initialized is True
    assert init["protocolVersion"] == 1

    sid = await adapter.new_session()
    assert sid == "sess_mock_1"

    stop = await adapter.prompt(sid, "summarise config.json", trace_id="t-abc")
    assert stop == "end_turn"

    # Give the reader loop a tick to drain any trailing notifications.
    await asyncio.sleep(0.05)
    await adapter.close()
    server_task.cancel()

    kinds = [c["kind"] for c in collected]

    # Every taOS reply kind we expect from this scripted turn appeared.
    assert "reasoning" in kinds  # thought + plan
    assert "delta" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "final" in kinds

    # --- Field-level parity assertions ---

    # Reasoning: thought text preserved.
    reasonings = [c for c in collected if c["kind"] == "reasoning"]
    assert any("read config.json" in c["content"].lower() for c in reasonings)
    # Plan rendered into a reasoning event.
    assert any(c["content"].startswith("Plan:") for c in reasonings)

    # Deltas accumulate the streamed assistant text.
    deltas = "".join(c["content"] for c in collected if c["kind"] == "delta")
    assert deltas == "Let me check that. Done."

    # The `final` reply carries the full accumulated assistant text (not "")
    # so sinks that don't reassemble deltas still get the complete message.
    finals = [c for c in collected if c["kind"] == "final"]
    assert len(finals) == 1
    assert finals[0]["content"] == "Let me check that. Done."

    # tool_call carries the tool title + rawInput as args (arg fidelity).
    tcs = [c for c in collected if c["kind"] == "tool_call" and not c["tool"].startswith("permission:")]
    assert len(tcs) == 1
    assert tcs[0]["tool"] == "Read config.json"
    assert tcs[0]["args"] == {"path": "config.json"}

    # Permission ask surfaced as a tool_call note.
    perms = [c for c in collected if c["kind"] == "tool_call" and c["tool"].startswith("permission:")]
    assert len(perms) == 1

    # tool_result pairs back to the tool title and carries output + success.
    trs = [c for c in collected if c["kind"] == "tool_result"]
    assert len(trs) == 1
    assert trs[0]["tool"] == "Read config.json"
    assert trs[0]["success"] is True
    assert trs[0]["result"] == {"k": 1}

    # Permission was answered with the policy-matching option.
    assert server.permission_outcome == {"outcome": {"outcome": "selected", "optionId": "o-allow"}}

    # All events share the active trace_id.
    assert all(c["trace_id"] == "t-abc" for c in collected)


@pytest.mark.asyncio
async def test_permission_reject_when_policy_no_match(collected):
    """If no option matches the policy, the adapter cancels the permission."""

    def sink(reply):
        collected.append(reply)

    pipe = _MemoryPipe()
    server = MockACPServer(pipe.a_to_b, pipe.b_writer)
    server_task = asyncio.create_task(server.run())

    # Policy 'allow_always' is offered by neither scripted option -> cancelled.
    cfg = ACPConfig(command=["mock"], permission_policy="allow_always", request_timeout=5)
    adapter = ACPAdapter(cfg, sink)
    await adapter.start(pipe.a_writer, pipe.b_to_a)
    await adapter.initialize()
    sid = await adapter.new_session()
    await adapter.prompt(sid, "go", trace_id="t-2")
    await asyncio.sleep(0.05)
    await adapter.close()
    server_task.cancel()

    assert server.permission_outcome == {"outcome": {"outcome": "cancelled"}}


@pytest.mark.asyncio
async def test_refusal_stop_reason_emits_error(collected):
    """A refusal stopReason produces a taOS error event."""

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

    adapter = ACPAdapter(ACPConfig(command=["mock"], request_timeout=5), sink)
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


class TestSessionBinding:
    """The ACP bridge must be launched bound to the agent's gateway session,
    or `session/prompt` hangs (validated live against OpenClaw 2026.4.18)."""

    def test_session_key_appended_to_command(self):
        from tinyagentos.adapters.acp_adapter import ACPAdapter, ACPConfig

        cfg = ACPConfig(command=["openclaw", "acp"], session_key="agent:main:main")
        ad = ACPAdapter(cfg, sink=lambda r: None)
        assert ad._effective_command() == ["openclaw", "acp", "--session", "agent:main:main"]

    def test_no_session_key_leaves_command_unchanged(self):
        from tinyagentos.adapters.acp_adapter import ACPAdapter, ACPConfig

        cfg = ACPConfig(command=["openclaw", "acp"])
        ad = ACPAdapter(cfg, sink=lambda r: None)
        assert ad._effective_command() == ["openclaw", "acp"]

    def test_existing_session_flag_not_duplicated(self):
        from tinyagentos.adapters.acp_adapter import ACPAdapter, ACPConfig

        cfg = ACPConfig(command=["openclaw", "acp", "--session", "x"], session_key="agent:main:main")
        ad = ACPAdapter(cfg, sink=lambda r: None)
        assert ad._effective_command().count("--session") == 1


@pytest.mark.asyncio
async def test_eof_fails_pending_requests_fast():
    """When the ACP server's stream closes, in-flight requests fail
    immediately with a transport error instead of hanging to request_timeout."""
    pipe = _MemoryPipe()
    client_writer, client_reader = pipe.client_io()
    adapter = ACPAdapter(ACPConfig(command=["mock"], request_timeout=30), sink=lambda r: None)
    await adapter.start(client_writer, client_reader)

    # Start a request, let it register its pending future + send, THEN kill the
    # stream — the read loop must reject the pending future on EOF.
    task = asyncio.create_task(adapter.initialize())
    await asyncio.sleep(0)
    pipe.b_to_a.feed_eof()

    with pytest.raises(ACPProtocolError):
        await asyncio.wait_for(task, timeout=2)

    await adapter.close()
