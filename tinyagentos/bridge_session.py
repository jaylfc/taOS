"""BridgeSessionRegistry — per-agent queue + accumulator for the openclaw bridge.

One queue per agent slug holds events that taOS needs to deliver to openclaw
(user_message, abort). openclaw subscribes once at startup via SSE and drains
the queue; replies from openclaw flow back through POST /reply and are written
to the trace store + broadcast via the chat hub.

Single-subscriber semantics: if openclaw reconnects, the old stream's generator
sees a sentinel and exits; the new connection replaces it.

Delta buffering: per (slug, trace_id) a StringIO-like accumulator collects
``delta`` payloads. On ``final`` the buffer is flushed into the trace record
and the final chat message is created/completed. This keeps the trace store
clean (one message_out per turn, not one per token).

Queue size: unbounded in MVP (user messages are low-rate and tiny). Mark as
future bounded-queue work if needed.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import uuid
from types import SimpleNamespace
from typing import Any, AsyncIterator

from tinyagentos.prompt_assembly import assemble_system_prompt

logger = logging.getLogger(__name__)


def build_bootstrap_system_prompt(agent) -> str:
    """Assemble the system prompt from an agent record.

    Accepts either a dict (YAML-loaded config shape) or any object exposing
    the expected attributes (``slug``, ``soul_md``, ``agent_md``,
    ``memory_plugin``). The dict's ``name`` field is used as ``slug`` since
    the taOS config uses ``name`` as the container-safe slug.
    """
    if isinstance(agent, dict):
        agent = SimpleNamespace(
            slug=agent.get("name"),
            soul_md=agent.get("soul_md", ""),
            agent_md=agent.get("agent_md", ""),
            memory_plugin=agent.get("memory_plugin", "taosmd"),
        )
    return assemble_system_prompt(agent)

# Sentinel pushed to a subscriber queue to tell its generator to exit.
_DISCONNECT = object()

TICK_INTERVAL = 15  # seconds between keepalive ticks


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _extract_decision_id(result: Any) -> str | None:
    """Extract decision_id from a request_decision tool_result.

    The result may arrive as a dict (``{"ok": True, "decision_id": "...", ...}``)
    or as a JSON string (openclaw serialises non-trivial payloads).  Any other
    shape or a missing key returns None.
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(result, dict):
        did = result.get("decision_id")
        if isinstance(did, str) and did:
            return did
    return None


class _AgentSession:
    """Per-agent mutable state held by the registry."""

    def __init__(self) -> None:
        # Queue of SSE payloads (dicts) to push to the openclaw subscriber.
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        # Maps trace_id -> StringIO accumulator for delta buffering.
        self._delta_buffers: dict[str, io.StringIO] = {}
        # Maps trace_id -> pending chat message id (created as 'streaming').
        self._pending_msg_ids: dict[str, str] = {}
        # Maps trace_id -> hops_since_user from the triggering user_message event.
        self.pending_hops: dict[str, int] = {}

    def accumulate_delta(self, trace_id: str, content: str) -> None:
        if trace_id not in self._delta_buffers:
            self._delta_buffers[trace_id] = io.StringIO()
        self._delta_buffers[trace_id].write(content)

    def flush_delta(self, trace_id: str) -> str:
        buf = self._delta_buffers.pop(trace_id, None)
        if buf is None:
            return ""
        return buf.getvalue()

    def set_pending_msg(self, trace_id: str, msg_id: str) -> None:
        self._pending_msg_ids[trace_id] = msg_id

    def pop_pending_msg(self, trace_id: str) -> str | None:
        return self._pending_msg_ids.pop(trace_id, None)


class BridgeSessionRegistry:
    """Holds one _AgentSession per slug. Thread-safe via asyncio.

    Dependencies passed at construction:
        trace_registry  — TraceStoreRegistry (app.state.trace_registry)
        chat_messages   — ChatMessageStore   (app.state.chat_messages)
        chat_channels   — ChatChannelStore   (app.state.chat_channels)
        chat_hub        — ChatHub            (app.state.chat_hub)

    All four are optional so unit tests can inject fakes or None.
    """

    def __init__(
        self,
        trace_registry=None,
        chat_messages=None,
        chat_channels=None,
        chat_hub=None,
        archive=None,
    ) -> None:
        self._trace_registry = trace_registry
        self._chat_messages = chat_messages
        self._chat_channels = chat_channels
        self._chat_hub = chat_hub
        self._archive = archive
        self._sessions: dict[str, _AgentSession] = {}
        self._lock = asyncio.Lock()
        self._router = None

    def _get_or_create(self, slug: str) -> _AgentSession:
        if slug not in self._sessions:
            self._sessions[slug] = _AgentSession()
        return self._sessions[slug]

    async def enqueue_user_message(self, slug: str, msg: dict) -> None:
        """Push a user_message event onto the agent's SSE queue.

        Called by the chat router when a message is dispatched to the
        openclaw-backed agent. Also records a ``message_in`` trace event so
        the agent's trace captures both sides of the conversation.
        """
        if not slug or slug == "_unknown_":
            # Defensive: never write orphan trace entries under a slug we
            # don't recognise. The chat router already validates via
            # find_agent() before calling here; this guard is a belt-and-
            # braces check.
            return
        async with self._lock:
            session = self._get_or_create(slug)
        trace_id = msg.get("trace_id") or msg.get("id")
        if trace_id is not None:
            session.pending_hops[trace_id] = int(msg.get("hops_since_user") or 0)
        await session.queue.put({
            "event": "user_message",
            "data": msg,
        })

        # Record a message_in trace event so the transcript captures both
        # sides in order. Envelope payload follows ENVELOPE_V1_SCHEMA's
        # message_in shape ({from, text, ...}); extra fields (message_id,
        # author_type, delivery) are informational.
        if self._trace_registry is None:
            return
        try:
            store = await self._trace_registry.get(slug)
            await store.record(
                "message_in",
                trace_id=msg.get("trace_id") or msg.get("id"),
                channel_id=msg.get("channel_id"),
                payload={
                    "from": msg.get("from", "user"),
                    "text": msg.get("text", ""),
                    "message_id": msg.get("id"),
                    "author_type": msg.get("author_type", "user"),
                    "delivery": "queued",
                },
            )
        except Exception:
            logger.exception(
                "bridge_session: message_in trace write failed for %s", slug,
            )

    async def subscribe(self, slug: str) -> AsyncIterator[str]:
        """Async generator yielding raw SSE text frames.

        Manages single-subscriber semantics: on entry, pushes _DISCONNECT
        into any existing queue so a stale generator exits promptly, then
        drains any pending items into the new queue so no user_messages
        already enqueued are lost. A background keepalive task fires tick
        events every TICK_INTERVAL seconds.
        """
        async with self._lock:
            session = self._get_or_create(slug)
            old_queue = session.queue
            new_queue: asyncio.Queue = asyncio.Queue()
            # Drain pending items (e.g. user messages enqueued before subscribe)
            # into the new queue before signalling the old subscriber.
            pending = []
            while not old_queue.empty():
                try:
                    item = old_queue.get_nowait()
                    if item is not _DISCONNECT:
                        pending.append(item)
                except asyncio.QueueEmpty:
                    break
            # Signal any existing subscriber to exit via its queue position.
            old_queue.put_nowait(_DISCONNECT)
            # Seed the new queue with any saved pending items.
            for item in pending:
                new_queue.put_nowait(item)
            session.queue = new_queue
            queue = new_queue

        tick_task: asyncio.Task | None = None

        async def _tick() -> None:
            while True:
                await asyncio.sleep(TICK_INTERVAL)
                await queue.put({"event": "tick", "data": {"ts": time.time()}})

        try:
            tick_task = asyncio.create_task(_tick())
            while True:
                item = await queue.get()
                if item is _DISCONNECT:
                    break
                event_type = item.get("event", "message")
                data = json.dumps(item.get("data", {}))
                yield f"event: {event_type}\ndata: {data}\n\n"
        finally:
            if tick_task is not None:
                tick_task.cancel()

    async def record_reply(self, slug: str, body: dict) -> None:
        """Process a reply POST from openclaw.

        Writes trace events, accumulates deltas, creates/updates chat messages,
        and broadcasts via the hub. All failures are caught and logged — never
        re-raises so the route always returns 202.
        """
        try:
            await self._handle_reply(slug, body)
        except Exception:
            logger.exception("bridge_session: record_reply error for agent %s", slug)

    async def _handle_reply(self, slug: str, body: dict) -> None:
        kind = body.get("kind", "")
        trace_id = body.get("trace_id") or _new_id()
        msg_id = body.get("id") or _new_id()
        content = body.get("content") or ""

        # Forks (e.g. openclaw) that don't thread channel_id through the reply
        # payload still send trace_id == originating message id, so we can
        # look up the original message and route the reply back to its source
        # channel rather than collapsing every reply into the agent's DM.
        if not body.get("channel_id") and trace_id and self._chat_messages:
            try:
                orig = await self._chat_messages.get_message(trace_id)
            except Exception:
                orig = None
            if orig and orig.get("channel_id"):
                body["channel_id"] = orig["channel_id"]

        async with self._lock:
            session = self._get_or_create(slug)

        if kind == "delta":
            session.accumulate_delta(trace_id, content)
            # Ensure a streaming placeholder chat message exists.
            pending_msg_id = session._pending_msg_ids.get(trace_id)
            if pending_msg_id is None and self._chat_messages and self._chat_channels:
                channel_id = body.get("channel_id") or await self._resolve_channel(slug)
                if channel_id:
                    _delta_hops = session.pending_hops.get(trace_id, 0)
                    new_msg = await self._chat_messages.send_message(
                        channel_id=channel_id,
                        author_id=slug,
                        author_type="agent",
                        content="",
                        state="streaming",
                        metadata={"trace_id": trace_id, "openclaw_msg_id": msg_id, "hops_since_user": _delta_hops},
                    )
                    session.set_pending_msg(trace_id, new_msg["id"])
                    if self._chat_channels:
                        await self._chat_channels.update_last_message_at(channel_id)
                    if self._chat_hub:
                        await self._chat_hub.broadcast(channel_id, {
                            "type": "message",
                            "seq": self._chat_hub.next_seq(),
                            **new_msg,
                        })
                    pending_msg_id = new_msg["id"]
            # Broadcast delta.
            if pending_msg_id and self._chat_hub:
                channel_id = body.get("channel_id") or await self._resolve_channel(slug)
                if channel_id:
                    await self._chat_hub.broadcast(channel_id, {
                        "type": "message_delta",
                        "seq": self._chat_hub.next_seq(),
                        "message_id": pending_msg_id,
                        "channel_id": channel_id,
                        "delta": content,
                    })

        elif kind == "final":
            accumulated = session.flush_delta(trace_id)
            final_content = content or accumulated
            pending_msg_id = session.pop_pending_msg(trace_id)
            channel_id = body.get("channel_id") or await self._resolve_channel(slug)

            # Write trace event.
            if self._trace_registry:
                store = await self._trace_registry.get(slug)
                await store.record(
                    "message_out",
                    trace_id=trace_id,
                    channel_id=channel_id,
                    payload={"content": final_content},
                )

            persisted = None
            if channel_id:
                if pending_msg_id and self._chat_messages:
                    # Edit the streaming placeholder to final content.
                    await self._chat_messages.edit_message(pending_msg_id, final_content)
                    await self._chat_messages.update_state(pending_msg_id, "complete")
                    session.pending_hops.pop(trace_id, None)
                    if self._chat_hub:
                        await self._chat_hub.broadcast(channel_id, {
                            "type": "message_edit",
                            "seq": self._chat_hub.next_seq(),
                            "message_id": pending_msg_id,
                            "content": final_content,
                            "edited_at": time.time(),
                        })
                        await self._chat_hub.broadcast(channel_id, {
                            "type": "message_state",
                            "seq": self._chat_hub.next_seq(),
                            "message_id": pending_msg_id,
                            "state": "complete",
                        })
                elif self._chat_messages and self._chat_channels:
                    # No streaming placeholder — create the message directly.
                    _final_hops = session.pending_hops.get(trace_id, 0)
                    session.pending_hops.pop(trace_id, None)
                    new_msg = await self._chat_messages.send_message(
                        channel_id=channel_id,
                        author_id=slug,
                        author_type="agent",
                        content=final_content,
                        state="complete",
                        metadata={"trace_id": trace_id, "openclaw_msg_id": msg_id, "hops_since_user": _final_hops},
                    )
                    await self._chat_channels.update_last_message_at(channel_id)
                    if self._chat_hub:
                        await self._chat_hub.broadcast(channel_id, {
                            "type": "message",
                            "seq": self._chat_hub.next_seq(),
                            **new_msg,
                        })
                    persisted = new_msg

            # Re-dispatch so other agents in the channel see this reply.
            router = self._router
            if router is not None and persisted is not None:
                try:
                    channel = await self._chat_channels.get_channel(persisted["channel_id"])
                    if channel is not None:
                        router.dispatch(persisted, channel)
                except Exception:
                    logger.warning("re-dispatch on agent reply failed", exc_info=True)

        elif kind == "tool_call":
            channel_id = body.get("channel_id") or await self._resolve_channel(slug)
            tool_name = body.get("tool") or ""
            if self._trace_registry:
                store = await self._trace_registry.get(slug)
                await store.record(
                    "tool_call",
                    trace_id=trace_id,
                    channel_id=channel_id,
                    payload={
                        "tool": tool_name,
                        "args": body.get("args") or {},
                        "caller": "openclaw",
                    },
                )
            if self._archive is not None:
                try:
                    await self._archive.record(
                        event_type="tool_call",
                        data={
                            "tool": tool_name,
                            "args": body.get("args") or {},
                            "trace_id": trace_id,
                        },
                        agent_name=slug,
                        summary=tool_name,
                    )
                except Exception:
                    logger.exception("archive dual-write failed (tool_call)")

        elif kind == "tool_result":
            channel_id = body.get("channel_id") or await self._resolve_channel(slug)
            tool_name = body.get("tool") or ""
            if self._trace_registry:
                store = await self._trace_registry.get(slug)
                await store.record(
                    "tool_result",
                    trace_id=trace_id,
                    channel_id=channel_id,
                    payload={
                        "tool": tool_name,
                        "result": body.get("result"),
                        "success": body.get("success", True),
                    },
                )
            if self._archive is not None:
                try:
                    await self._archive.record(
                        event_type="tool_result",
                        data={
                            "tool": tool_name,
                            "result": body.get("result"),
                            "success": body.get("success", True),
                            "trace_id": trace_id,
                        },
                        agent_name=slug,
                        summary=tool_name,
                    )
                except Exception:
                    logger.exception("archive dual-write failed (tool_result)")

            # Attach a read-only decision content block to the in-flight chat
            # message when the agent raises a decision via the request_decision
            # tool. The block references only the decision id so the frontend
            # can fetch the full decision and render it inline (read-only).
            if tool_name == "request_decision":
                decision_id = _extract_decision_id(body.get("result"))
                if decision_id and self._chat_messages and self._chat_hub:
                    msg_id = session._pending_msg_ids.get(trace_id)
                    if msg_id:
                        try:
                            blocks = await self._chat_messages.append_content_block(
                                msg_id,
                                {"kind": "decision", "decision_id": decision_id},
                            )
                            if blocks and channel_id:
                                await self._chat_hub.broadcast(channel_id, {
                                    "type": "message_edit",
                                    "seq": self._chat_hub.next_seq(),
                                    "message_id": msg_id,
                                    "content_blocks": blocks,
                                })
                        except Exception:
                            logger.exception(
                                "bridge_session: failed to attach decision block "
                                "for decision %s on message %s",
                                decision_id, msg_id,
                            )

        elif kind == "error":
            error_text = body.get("error") or ""
            channel_id = body.get("channel_id") or await self._resolve_channel(slug)
            if self._trace_registry:
                store = await self._trace_registry.get(slug)
                await store.record(
                    "error",
                    trace_id=trace_id,
                    channel_id=channel_id,
                    payload={"stage": "openclaw", "message": error_text},
                )
            if self._archive is not None:
                try:
                    await self._archive.record(
                        event_type="error",
                        data={
                            "error": error_text,
                            "trace_id": trace_id,
                        },
                        agent_name=slug,
                        summary=error_text,
                    )
                except Exception:
                    logger.exception("archive dual-write failed (error)")
            # Set any pending message to error state.
            pending_msg_id = session.pop_pending_msg(trace_id)
            if pending_msg_id and self._chat_messages:
                await self._chat_messages.update_state(pending_msg_id, "error")
                if channel_id and self._chat_hub:
                    await self._chat_hub.broadcast(channel_id, {
                        "type": "message_state",
                        "seq": self._chat_hub.next_seq(),
                        "message_id": pending_msg_id,
                        "state": "error",
                    })

        elif kind == "reasoning":
            channel_id = body.get("channel_id") or await self._resolve_channel(slug)
            if self._trace_registry:
                store = await self._trace_registry.get(slug)
                await store.record(
                    "reasoning",
                    trace_id=trace_id,
                    channel_id=channel_id,
                    payload={"text": content},
                )
            if self._archive is not None:
                try:
                    await self._archive.record(
                        event_type="reasoning",
                        data={
                            "text": content,
                            "trace_id": trace_id,
                        },
                        agent_name=slug,
                        summary=content[:120] if content else "",
                    )
                except Exception:
                    logger.exception("archive dual-write failed (reasoning)")
        # "delta" without a matching kind is silently ignored.

    async def _resolve_channel(self, slug: str) -> str | None:
        """Return the DM channel id for the agent, or None if unavailable."""
        if not self._chat_channels:
            return None
        try:
            channels = await self._chat_channels.list_channels(member_id=slug)
            for ch in channels:
                if ch.get("type") == "dm":
                    return ch["id"]
            # Fall back: return first channel the agent is a member of.
            if channels:
                return channels[0]["id"]
        except Exception:
            logger.debug("bridge_session: channel lookup failed for %s", slug)
        return None
