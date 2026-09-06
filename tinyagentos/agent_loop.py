"""Agent loop: subagent delegation + safe-point message queue.

The main chat agent delegates heavy/long work to subagents so its own loop
stays free to present results and accept user interrupts/redirects. Messages
that arrive while the agent is working are queued (never dropped, never
applied mid-step) and surfaced at the next safe boundary -- a turn is atomic,
so interrupting mid-tool-call corrupts state. A redirect cancels in-flight
subagent work at that safe boundary.

Public surface:
    AgentLoop          -- the loop controller
    LoopState          -- idle / working / safe_point
    LoopAction         -- immediate / queued (return of ``handle_message``)
    QueuedMessage      -- a buffered user message
    SubagentHandle     -- status of a spawned subagent
    ProgressCallback   -- callable for streaming subagent progress
    SubagentWorker     -- async callable that performs a subagent's work
"""
from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from tinyagentos.task_utils import _create_supervised_task, cancel_and_wait

logger = logging.getLogger(__name__)

# A sync callable that a subagent invokes to stream partial results (reply
# dicts with ``kind``/``content``/etc.) back to the main loop's sink.
ProgressCallback = Callable[[dict], None]

# An async callable that does the subagent's heavy work. It receives a
# progress callback so it can stream intermediate results without blocking the
# main loop. Returning a value completes the subagent with that result.
SubagentWorker = Callable[[ProgressCallback], Awaitable[Any]]


# ---------------------------------------------------------------------------
# Enums and dataclasses
# ---------------------------------------------------------------------------

class LoopState(enum.Enum):
    """High-level loop state at any moment.

    IDLE        -- no turn in progress; new messages start a turn immediately.
    WORKING     -- a turn (or one or more subagents) is in flight; new
                   messages are queued for the next safe point.
    SAFE_POINT  -- transient; the turn just ended and queued messages are
                   being surfaced. The loop returns to IDLE right after.
    """

    IDLE = "idle"
    WORKING = "working"
    SAFE_POINT = "safe_point"


class LoopAction(enum.Enum):
    """What :meth:`AgentLoop.handle_message` decided to do with a message."""

    IMMEDIATE = "immediate"
    QUEUED = "queued"


@dataclass
class QueuedMessage:
    """A user message buffered while the loop was busy.

    ``is_redirect`` marks a message that, when surfaced at a safe point,
    cancels in-flight subagent work before the next turn starts.
    """

    id: str
    content: str
    received_at: float
    is_redirect: bool = False


@dataclass
class SubagentHandle:
    """Tracks a spawned subagent from creation through completion.

    ``state`` is one of ``"running"``, ``"completed"``, ``"cancelled"`` or
    ``"failed"``. ``result``/``error`` are populated on the latter three.
    """

    id: str
    task: str
    state: str = "running"
    result: Any = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)


class _SubagentEntry:
    """Internal pair of a handle and its backing asyncio task."""

    __slots__ = ("handle", "task_obj")

    def __init__(
        self,
        handle: SubagentHandle,
        task_obj: asyncio.Task | None = None,
    ) -> None:
        self.handle = handle
        self.task_obj = task_obj


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------

class AgentLoop:
    """Main agent loop with subagent delegation and safe-point message queue.

    Responsibilities (matching the task spec):
        (1) Agents can spawn subagents for long tasks so the main loop stays
            responsive.
        (2) Messages arriving while working are queued -- never dropped, never
            applied mid-step.
        (3) At the next safe boundary queued messages are surfaced. A redirect
            among them cancels in-flight subagent work.
        (4) ``status()`` lets the user see what is running and what is queued.

    A *turn* is atomic: once ``handle_message`` returns ``IMMEDIATE`` the loop
    is in ``WORKING`` state until :meth:`reach_safe_point` is called. Any
    message that arrives in between is buffered. The caller drives the turn
    (e.g. via :func:`tinyagentos.opencode_runtime.drive_turn` or an ACP turn)
    and calls :meth:`reach_safe_point` at the end to drain the queue.
    """

    def __init__(self, sink: Callable[[dict], Any] | None = None) -> None:
        """Create a new agent loop.

        Args:
            sink: Optional async-or-sync callable that receives subagent progress
                  dicts (same reply-dict shape used elsewhere in taOS). May be
                  None if no streaming is required.
        """
        self._state: LoopState = LoopState.IDLE
        self._message_queue: list[QueuedMessage] = []
        self._subagents: dict[str, _SubagentEntry] = {}
        self._lock = asyncio.Lock()
        self._sink = sink
        # Messages surfaced at safe points, retained for inspection.
        self._delivered: list[QueuedMessage] = []
        self._current_turn_id: str | None = None
        self._subagent_tasks: set[asyncio.Task] = set()
        self._max_delivered = 1000
        self._max_subagents = 100

    # ----------------------------------------------------------------- state

    @property
    def state(self) -> LoopState:
        """Current loop state."""
        return self._state

    @property
    def current_turn_id(self) -> str | None:
        """The message id at the head of the current turn, or None when idle."""
        return self._current_turn_id

    @property
    def message_queue(self) -> list[QueuedMessage]:
        """Snapshot of messages currently queued (not yet surfaced)."""
        return list(self._message_queue)

    @property
    def delivered(self) -> list[QueuedMessage]:
        """Messages surfaced at safe points, in delivery order."""
        return list(self._delivered)

    def has_pending_redirect(self) -> bool:
        """True if a redirect is buffered and waiting at the next safe point."""
        return any(m.is_redirect for m in self._message_queue)

    def _prune_subagents(self) -> None:
        if len(self._subagents) <= self._max_subagents:
            return
        to_remove = [
            sid for sid, entry in self._subagents.items()
            if entry.task_obj is not None and entry.task_obj.done()
        ]
        for sid in to_remove:
            del self._subagents[sid]

    def _prune_delivered(self) -> None:
        if len(self._delivered) > self._max_delivered:
            self._delivered = self._delivered[-self._max_delivered:]

    # ------------------------------------------------------ message handling

    async def handle_message(
        self,
        content: str,
        msg_id: str | None = None,
        is_redirect: bool = False,
    ) -> LoopAction:
        """Accept a user message for the main agent.

        If the loop is IDLE, the message starts a new turn immediately and the
        loop transitions to ``WORKING`` (returns ``IMMEDIATE``). The caller then
        drives the turn and eventually calls :meth:`reach_safe_point`.

        If the loop is already ``WORKING``, the message is appended to the
        queue and delivered at the next safe point (returns ``QUEUED``).
        It is never dropped and never applied mid-step.
        """
        msg_id = msg_id or uuid.uuid4().hex
        msg = QueuedMessage(
            id=msg_id,
            content=content,
            received_at=time.time(),
            is_redirect=is_redirect,
        )
        async with self._lock:
            if self._state in (LoopState.WORKING, LoopState.SAFE_POINT):
                self._message_queue.append(msg)
                return LoopAction.QUEUED
            # IDLE -> start a new turn. The queue is already drained by
            # reach_safe_point, so nothing is discarded here.
            self._state = LoopState.WORKING
            self._current_turn_id = msg_id
        return LoopAction.IMMEDIATE

    # ----------------------------------------------------- subagent lifecycle

    async def spawn_subagent(
        self,
        task: str,
        worker: SubagentWorker,
    ) -> str:
        """Spawn a subagent for heavy/long work.

        The subagent runs concurrently as a supervised background task. The
        main loop stays responsive -- it can accept messages, surface results
        via ``sink``, and eventually reach a safe point -- while the subagent
        works.

        Args:
            task:   Human-readable description of the delegated work.
            worker: ``async def worker(progress: ProgressCallback) -> result``
                    that performs the work. Call ``progress({...})`` to stream
                    partial results to the main loop's sink.

        Returns:
            The subagent id (use with :meth:`await_subagent` or :meth:`status`).
        """
        sub_id = uuid.uuid4().hex[:12]
        handle = SubagentHandle(id=sub_id, task=task, state="running")
        entry = _SubagentEntry(handle)
        async with self._lock:
            self._subagents[sub_id] = entry

        def _progress(msg: dict) -> None:
            """Forward subagent progress to the main loop's sink."""
            if self._sink is None:
                return
            try:
                res = self._sink(msg)
                if asyncio.iscoroutine(res):
                    _create_supervised_task(res, self._subagent_tasks)
            except Exception:
                logger.exception("subagent %s: progress sink raised", sub_id)

        async def _runner() -> None:
            try:
                result = await worker(_progress)
                async with self._lock:
                    handle.state = "completed"
                    handle.result = result
            except asyncio.CancelledError:
                async with self._lock:
                    handle.state = "cancelled"
                raise
            except Exception as exc:
                async with self._lock:
                    handle.state = "failed"
                    handle.error = str(exc)
                logger.exception("subagent %s (%s) failed", sub_id, task)
                raise

        task_obj = _create_supervised_task(_runner(), self._subagent_tasks)
        task_obj.set_name(f"subagent:{sub_id}")
        entry.task_obj = task_obj
        self._prune_subagents()
        logger.debug("subagent %s spawned for task=%s", sub_id, task)
        return sub_id

    async def cancel_subagents(self, reason: str | None = None) -> int:
        """Cancel all in-flight subagents.

        Called when a redirect arrives at a safe boundary. Cancellation is
        requested on every running subagent task and awaited with a bounded
        timeout so a misbehaving worker cannot block the loop forever. The
        worker itself decides how to unwind (cooperative cancellation via
        ``CancelledError``).

        Returns:
            The number of subagents that were running and had cancellation
            requested.
        """
        to_cancel: list[asyncio.Task] = []
        async with self._lock:
            for entry in self._subagents.values():
                if entry.handle.state == "running" and entry.task_obj is not None:
                    if not entry.task_obj.done():
                        to_cancel.append(entry.task_obj)
                        entry.task_obj.cancel()
        # Wait outside the lock so the loop isn't held while workers unwind.
        if to_cancel:
            stragglers = await cancel_and_wait(to_cancel, timeout=10.0)
            for t in stragglers:
                logger.warning(
                    "subagent %s did not exit within 10s after cancel",
                    t.get_name(),
                )
        count = len(to_cancel)
        if reason:
            logger.info("subagent: cancelled %d task(s) -- %s", count, reason)
        return count

    # --------------------------------------------------------- safe boundary

    async def reach_safe_point(self) -> list[QueuedMessage]:
        """Transition to SAFE_POINT and surface queued messages.

        Called at a turn boundary: the turn is atomic and complete. The loop
        moves ``WORKING`` -> ``SAFE_POINT``, drains the message queue so the
        caller can act on each buffered message, then returns to ``IDLE`` so
        new messages are processed immediately on the next turn.

        Messages that arrive while ``cancel_subagents`` is running are queued
        safely (the loop stays in ``SAFE_POINT``) and included in the returned
        list.

        Returns:
            The queued messages to surface, in arrival order. Callers iterate
            these and start a fresh turn for each (or redirect as appropriate).
        """
        async with self._lock:
            self._state = LoopState.SAFE_POINT
            queued = list(self._message_queue)
            self._message_queue.clear()

        # If a redirect is among the queued messages, cancel subagents at this
        # safe boundary (never mid-turn).
        if any(m.is_redirect for m in queued):
            await self.cancel_subagents(reason="redirect at safe point")

        async with self._lock:
            queued.extend(self._message_queue)
            self._message_queue.clear()
            if self._state == LoopState.SAFE_POINT:
                self._state = LoopState.IDLE
                self._current_turn_id = None
            self._delivered.extend(queued)
            self._prune_delivered()
        return queued

    # --------------------------------------------------- visibility / status

    def status(self) -> dict[str, Any]:
        """Return what is running and what is queued (for UI visibility).

        Lets the user see:
        - ``state``: current loop state (idle / working / safe_point)
        - ``current_turn_id``: the message id driving the current turn
        - ``subagents``: list of subagent descriptors (id, task, state, ...)
        - ``queued_messages``: list of buffered message descriptors
        - ``queued_count``: how many messages are buffered

        Safe to call from any context (no I/O, no await).
        """
        subagents: list[dict[str, Any]] = []
        for sid, entry in self._subagents.items():
            h = entry.handle
            subagents.append({
                "id": sid,
                "task": h.task,
                "state": h.state,
                "started_at": h.started_at,
                "result": h.result,
                "error": h.error,
            })
        queued: list[dict[str, Any]] = [
            {
                "id": m.id,
                "content": m.content,
                "received_at": m.received_at,
                "is_redirect": m.is_redirect,
            }
            for m in self._message_queue
        ]
        return {
            "state": self._state.value,
            "current_turn_id": self._current_turn_id,
            "subagents": subagents,
            "queued_messages": queued,
            "queued_count": len(self._message_queue),
        }

    # --------------------------------------------------- waiting for subagents

    async def await_subagent(
        self,
        sub_id: str,
        timeout: float | None = None,
    ) -> Any:
        """Wait for a subagent to finish and return its result.

        If the subagent was cancelled (e.g. via ``cancel_subagents`` or a
        redirect at a safe point), the handle state is ``"cancelled"`` and
        the result (``None``) is returned without re-raising. If the worker
        raised, that exception is re-raised here at the caller.

        A *timeout* only limits how long this call waits. It does NOT cancel
        the subagent task. The subagent keeps running and can be awaited again
        later.

        Raises:
            KeyError: if *sub_id* is unknown.
            asyncio.TimeoutError: if *timeout* is given and exceeded.
            Exception: the worker's own exception, if the subagent failed.
        """
        async with self._lock:
            entry = self._subagents.get(sub_id)
        if entry is None or entry.task_obj is None:
            raise KeyError(sub_id)
        if entry.task_obj.done():
            if entry.handle.state == "cancelled":
                return entry.handle.result
            if entry.handle.state == "failed":
                raise entry.task_obj.exception()
            return entry.handle.result
        done, pending = await asyncio.wait([entry.task_obj], timeout=timeout)
        if entry.task_obj in pending:
            raise asyncio.TimeoutError(
                f"subagent {sub_id} did not finish within {timeout}s"
            )
        if entry.handle.state == "cancelled":
            return entry.handle.result
        if entry.handle.state == "failed":
            raise entry.task_obj.exception()
        return entry.handle.result

    def get_subagent(self, sub_id: str) -> SubagentHandle | None:
        """Return a snapshot of a subagent's handle, or None if unknown."""
        entry = self._subagents.get(sub_id)
        if entry is None:
            return None
        h = entry.handle
        return SubagentHandle(
            id=h.id,
            task=h.task,
            state=h.state,
            result=h.result,
            error=h.error,
            started_at=h.started_at,
        )

    async def await_all_subagents(self, timeout: float | None = None) -> None:
        """Wait for all running subagents to settle (success or cancel).

        Does not cancel them -- just waits. A *timeout* only limits how long
        this call waits. It does NOT cancel any subagent tasks. Tasks keep
        running and can be awaited individually after the timeout.

        Useful before the loop can safely reach a safe point and go idle.

        Raises:
            asyncio.TimeoutError: if *timeout* is given and any subagent did
                not finish within it.
        """
        async with self._lock:
            tasks = [
                e.task_obj
                for e in self._subagents.values()
                if e.task_obj is not None and not e.task_obj.done()
            ]
        if not tasks:
            return None
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            raise asyncio.TimeoutError(
                f"{len(pending)} subagent(s) did not finish within {timeout}s"
            )
        self._prune_subagents()
        return None
