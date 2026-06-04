"""ACP adapter — fork-free OpenClaw integration via the Agent Client Protocol.

SPIKE / PROTOTYPE. This module de-risks dropping taOS's OpenClaw *fork* (the
in-process ``taos-bridge.ts`` patch) in favour of talking to OpenClaw — or any
ACP-speaking agent — over its published wire protocol.

ACP (Zed's Agent Client Protocol, https://agentclientprotocol.com) is JSON-RPC
2.0 over stdio. OpenClaw ships ``openclaw acp`` (the ``@openclaw/acpx`` runtime).
The handshake is::

    initialize        -> negotiate protocolVersion + capabilities
    session/new       -> { sessionId }
    session/prompt    -> drives a turn; agent streams session/update
                         notifications until it returns { stopReason }

While a turn runs the agent emits ``session/update`` notifications. This adapter
consumes them and maps each onto a taOS *trace-event kind* — the exact ``kind``
strings that ``tinyagentos/bridge_session.py``'s ``record_reply`` already
understands (delta, final, tool_call, tool_result, reasoning, error). That means
taOS keeps its existing trace/observability contract without forking OpenClaw:
the fork's hand-written bridge becomes a standards-based protocol client.

Mapping (ACP session/update -> taOS reply kind):

    agent_message_chunk        -> delta        (streamed assistant text)
    agent_thought_chunk        -> reasoning    (model thoughts/deliberation)
    plan                       -> reasoning    (plan entries, summarised)
    tool_call (pending/...)    -> tool_call    (title + rawInput as args)
    tool_call_update completed -> tool_result  (rawOutput/content + success)
    tool_call_update failed    -> tool_result  (success=False)
    (turn end, stopReason)     -> final        (flush accumulated text)
    (stopReason=refusal/error) -> error

``session/request_permission`` is a *request* (expects a response), not a
notification. The adapter answers it with a configurable auto policy so a
headless taOS turn does not deadlock, and surfaces it as a ``tool_call``-shaped
trace note for visibility.

The adapter is framework-agnostic and config-driven: give it a ``command`` and
``args`` (e.g. ``["openclaw", "acp"]``) and a sink callback that receives taOS
reply dicts. It does not import OpenClaw or FastAPI; the sink is where a caller
wires ``BridgeSessionRegistry.record_reply`` (or an HTTP POST to the reply URL).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# A sink receives taOS reply dicts (the body shape record_reply consumes) and
# may be sync or async.
ReplySink = Callable[[dict], Any]

# Default answer to session/request_permission so a headless turn never hangs.
# "reject_once" is the safe default for an unattended controller; callers can
# override to allow. We answer with the first option whose kind matches.
DEFAULT_PERMISSION = "reject_once"


@dataclass
class ACPConfig:
    """Config for launching and driving an ACP server subprocess."""

    command: list[str]  # e.g. ["openclaw", "acp"] or ["npx", "openclaw", "acp"]
    cwd: Optional[str] = None
    env: Optional[dict[str, str]] = None
    protocol_version: int = 1
    # Gateway session key to bind the ACP bridge to (e.g. "agent:main:main").
    # CRITICAL for OpenClaw: without it, `session/new` creates an unbound
    # session with no model, and `session/prompt` hangs forever. Validated live
    # against OpenClaw 2026.4.18 — binding to the agent's session is what makes
    # a turn complete. When set, `--session <key>` is appended to the command.
    session_key: Optional[str] = None
    # How to answer session/request_permission unattended.
    permission_policy: str = DEFAULT_PERMISSION  # allow_once|allow_always|reject_once|reject_always
    request_timeout: float = 120.0
    client_capabilities: dict = field(
        default_factory=lambda: {"fs": {"readTextFile": False, "writeTextFile": False}}
    )


class ACPProtocolError(RuntimeError):
    """Raised when the ACP server returns a JSON-RPC error to a request."""


class ACPAdapter:
    """Drives one ACP server subprocess and maps its updates to taOS replies.

    Lifecycle:
        adapter = ACPAdapter(config, sink)
        await adapter.start(stdin, stdout)   # or .spawn() to launch subprocess
        await adapter.initialize()
        sid = await adapter.new_session()
        stop_reason = await adapter.prompt(sid, "hello", trace_id="t1")
        await adapter.close()

    ``sink`` is called once per mapped taOS reply event with a dict carrying at
    least ``kind`` and ``trace_id`` plus kind-specific fields, matching
    bridge_session.record_reply's body contract.
    """

    def __init__(self, config: ACPConfig, sink: ReplySink) -> None:
        self._cfg = config
        self._sink = sink
        self._proc: asyncio.subprocess.Process | None = None
        self._stdin: asyncio.StreamWriter | None = None
        self._stdout: asyncio.StreamReader | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        # JSON-RPC id -> Future for in-flight outbound requests.
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        # Accumulates assistant text across agent_message_chunk updates so the
        # turn's `final` reply carries the full message (not just the deltas).
        self._assistant_buf = ""
        # The trace_id of the turn currently in flight (for mapping updates).
        self._active_trace: str | None = None
        # Per-tool-call accumulated state so a tool_call_update can be paired
        # with the originating tool's name/title.
        self._tool_titles: dict[str, str] = {}
        self._started = asyncio.Event()

    # ------------------------------------------------------------------ I/O

    def _effective_command(self) -> list[str]:
        """The launch command, with ``--session <key>`` appended when a
        session_key is configured and not already present. Binding the ACP
        bridge to the agent's gateway session is required for turns to run."""
        cmd = list(self._cfg.command)
        if self._cfg.session_key and "--session" not in cmd:
            cmd += ["--session", self._cfg.session_key]
        return cmd

    async def spawn(self) -> None:
        """Launch the configured ACP server as a subprocess over stdio."""
        self._proc = await asyncio.create_subprocess_exec(
            *self._effective_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cfg.cwd,
            env=self._cfg.env,
        )
        assert self._proc.stdin and self._proc.stdout
        # Drain stderr continuously. If we pipe it but never read, a chatty
        # server fills the OS pipe buffer and the child deadlocks. We log lines
        # at debug for diagnostics rather than discarding blind.
        if self._proc.stderr is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc.stderr))
        await self.start(self._proc.stdin, self._proc.stdout)

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                logger.debug("acp stderr: %s", line.decode("utf-8", "replace").rstrip()[:300])
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def start(
        self, stdin: asyncio.StreamWriter, stdout: asyncio.StreamReader
    ) -> None:
        """Attach to an already-open stdio pair (used by tests with a stub)."""
        self._stdin = stdin
        self._stdout = stdout
        self._reader_task = asyncio.create_task(self._read_loop())
        self._started.set()

    def _fail_pending(self, exc: Exception) -> None:
        """Reject every in-flight request so callers fail fast instead of
        blocking until request_timeout when the transport dies."""
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
        # Don't leave callers awaiting a response that can never arrive.
        self._fail_pending(ACPProtocolError("ACP transport closed"))
        if self._stdin:
            try:
                self._stdin.close()
            except Exception:
                pass
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass

    # ------------------------------------------------------- JSON-RPC plumbing

    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _send(self, obj: dict) -> None:
        assert self._stdin is not None
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        self._stdin.write(line.encode("utf-8"))
        await self._stdin.drain()

    async def _request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and await its response."""
        rid = self._alloc_id()
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            result = await asyncio.wait_for(fut, timeout=self._cfg.request_timeout)
        finally:
            self._pending.pop(rid, None)
        return result

    async def _notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _respond(self, rid: Any, result: dict) -> None:
        await self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    async def _read_loop(self) -> None:
        assert self._stdout is not None
        while True:
            line = await self._stdout.readline()
            if not line:
                # EOF — server exited. Reject in-flight requests immediately so
                # initialize()/new_session()/prompt() fail fast, not on timeout.
                self._fail_pending(ACPProtocolError("ACP server closed the stream"))
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("acp: non-JSON line from server: %r", line[:200])
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict) -> None:
        # Response to one of our requests.
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.get(msg["id"])
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(ACPProtocolError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result", {}))
            return
        # Inbound request FROM the agent (expects a response).
        method = msg.get("method")
        if method and "id" in msg:
            await self._handle_server_request(msg)
            return
        # Notification from the agent (no id).
        if method:
            await self._handle_notification(method, msg.get("params", {}))

    # ----------------------------------------------------- handshake + prompt

    async def initialize(self) -> dict:
        return await self._request(
            "initialize",
            {
                "protocolVersion": self._cfg.protocol_version,
                "clientCapabilities": self._cfg.client_capabilities,
            },
        )

    async def new_session(self, cwd: str | None = None, mcp_servers: list | None = None) -> str:
        result = await self._request(
            "session/new",
            {"cwd": cwd or self._cfg.cwd or ".", "mcpServers": mcp_servers or []},
        )
        sid = result.get("sessionId")
        if not sid:
            raise ACPProtocolError(f"session/new returned no sessionId: {result}")
        return sid

    async def prompt(self, session_id: str, text: str, trace_id: str | None = None) -> str:
        """Run one turn. Returns the stopReason. Streams mapped events to sink."""
        trace = trace_id or uuid.uuid4().hex
        self._active_trace = trace
        self._assistant_buf = ""
        try:
            result = await self._request(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            )
            stop_reason = result.get("stopReason", "end_turn")
            # Flush the accumulated assistant text as the turn's final message,
            # so sinks that don't reassemble deltas still get the full reply.
            await self._emit({"kind": "final", "trace_id": trace, "content": self._assistant_buf})
            # Per the module contract, both refusal and error stop reasons are
            # surfaced as an error event (not just refusal).
            if stop_reason in ("refusal", "error"):
                await self._emit(
                    {"kind": "error", "trace_id": trace, "error": f"agent stopped: {stop_reason}"}
                )
            logger.debug("acp turn %s ended: stopReason=%s", trace, stop_reason)
            return stop_reason
        finally:
            # Always clear turn state, even on timeout/EOF/JSON-RPC error, so a
            # failed turn can't poison the next one with stale trace/tool data.
            self._active_trace = None
            self._tool_titles.clear()
            self._assistant_buf = ""

    # ----------------------------------------------- notification -> taOS map

    async def _handle_notification(self, method: str, params: dict) -> None:
        if method != "session/update":
            logger.debug("acp: ignoring notification %s", method)
            return
        update = params.get("update", {})
        kind = update.get("sessionUpdate")
        trace = self._active_trace or params.get("sessionId") or uuid.uuid4().hex

        if kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            if text:
                # Accumulate for the turn's `final` reply, and stream as a delta.
                if trace == self._active_trace:
                    self._assistant_buf += text
                await self._emit({"kind": "delta", "trace_id": trace, "content": text})

        elif kind == "agent_thought_chunk":
            text = _content_text(update.get("content"))
            if text:
                await self._emit({"kind": "reasoning", "trace_id": trace, "content": text})

        elif kind == "plan":
            summary = _plan_text(update.get("entries") or update.get("plan", {}).get("entries"))
            if summary:
                await self._emit({"kind": "reasoning", "trace_id": trace, "content": summary})

        elif kind == "tool_call":
            tc = update  # ToolCall fields are inline on the update
            tool_id = tc.get("toolCallId", "")
            title = tc.get("title") or tc.get("kind") or "tool"
            self._tool_titles[tool_id] = title
            await self._emit(
                {
                    "kind": "tool_call",
                    "trace_id": trace,
                    "tool": title,
                    "args": tc.get("rawInput") or {},
                }
            )

        elif kind == "tool_call_update":
            status = update.get("status")
            tool_id = update.get("toolCallId", "")
            title = self._tool_titles.get(tool_id, "tool")
            if status in ("completed", "failed"):
                await self._emit(
                    {
                        "kind": "tool_result",
                        "trace_id": trace,
                        "tool": title,
                        "result": update.get("rawOutput")
                        or _tool_content_text(update.get("content")),
                        "success": status == "completed",
                    }
                )
            # pending/in_progress updates are progress noise; taOS has no kind
            # for them, so they are intentionally dropped.

        else:
            # user_message_chunk / available_commands_update / current_mode_update
            logger.debug("acp: no taOS mapping for sessionUpdate=%s", kind)

    async def _handle_server_request(self, msg: dict) -> None:
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params", {})
        if method == "session/request_permission":
            await self._handle_permission(rid, params)
        elif method in ("fs/read_text_file", "fs/write_text_file"):
            # We declared no fs capability; reject politely so the agent moves on.
            await self._send(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "fs disabled"}}
            )
        else:
            logger.debug("acp: unhandled server request %s", method)
            await self._send(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unsupported"}}
            )

    async def _handle_permission(self, rid: Any, params: dict) -> None:
        trace = self._active_trace or params.get("sessionId") or uuid.uuid4().hex
        tool_call = params.get("toolCall", {})
        options = params.get("options", []) or []
        # Surface the permission ask as a trace note (tool_call-shaped).
        await self._emit(
            {
                "kind": "tool_call",
                "trace_id": trace,
                "tool": f"permission:{tool_call.get('title') or tool_call.get('kind') or 'op'}",
                "args": {"requested": True, "policy": self._cfg.permission_policy},
            }
        )
        # Pick the option matching our policy; else cancel.
        chosen = next(
            (o for o in options if o.get("kind") == self._cfg.permission_policy), None
        )
        if chosen is not None:
            outcome = {"outcome": "selected", "optionId": chosen.get("optionId")}
        else:
            outcome = {"outcome": "cancelled"}
        await self._respond(rid, {"outcome": outcome})

    # --------------------------------------------------------------- sink

    async def _emit(self, reply: dict) -> None:
        try:
            res = self._sink(reply)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("acp: sink raised for reply kind=%s", reply.get("kind"))


# ----------------------------------------------------------- content helpers


def _content_text(content: Any) -> str:
    """Extract text from an ACP ContentBlock (or list of them)."""
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(_content_text(c) for c in content)
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text", "")
        # resource with embedded text
        res = content.get("resource")
        if isinstance(res, dict) and "text" in res:
            return res["text"]
    if isinstance(content, str):
        return content
    return ""


def _tool_content_text(content: Any) -> str:
    """Extract text from ToolCallContent[] (each is {type:'content', content:<block>})."""
    if not isinstance(content, list):
        return _content_text(content)
    out = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "content":
            out.append(_content_text(item.get("content")))
        else:
            out.append(_content_text(item))
    return "".join(out)


def _plan_text(entries: Any) -> str:
    """Render a Plan's entries into a readable reasoning string."""
    if not isinstance(entries, list):
        return ""
    lines = []
    for e in entries:
        if isinstance(e, dict):
            status = e.get("status", "")
            content = e.get("content", "")
            mark = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}.get(status, "-")
            lines.append(f"{mark} {content}".strip())
    return "Plan:\n" + "\n".join(lines) if lines else ""
