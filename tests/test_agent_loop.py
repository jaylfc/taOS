"""Tests for the agent loop: subagent delegation + safe-point message queue.

Covers the three acceptance-test categories from the task:
  - queue-not-drop: a message sent mid-task is queued, never lost.
  - safe-point-delivery: a queued message is delivered at the safe boundary,
    never applied mid-step.
  - cancel-propagation: a redirect cancels in-flight subagent work.

Plus visibility (what-is-running / what-is-queued) and the subagent lifecycle.
"""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.agent_loop import (
    AgentLoop,
    LoopAction,
    LoopState,
    QueuedMessage,
    SubagentHandle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _RecordingSink:
    """Collects progress dicts the subagent streams back via the sink."""

    def __init__(self):
        self.received: list[dict] = []

    def __call__(self, msg: dict):
        self.received.append(msg)


async def _sleepy_worker(started: asyncio.Event, cancelled: asyncio.Event):
    """A subagent worker that runs until cancelled, signalling both events."""

    started.set()
    try:
        await asyncio.sleep(100)
    except asyncio.CancelledError:
        cancelled.set()
        raise


async def _slow_cancel_worker(started: asyncio.Event, cancelled: asyncio.Event):
    """A subagent worker that delays after receiving cancellation."""

    started.set()
    try:
        await asyncio.sleep(100)
    except asyncio.CancelledError:
        await asyncio.sleep(0.2)
        cancelled.set()
        raise


async def _quick_worker(result="done"):
    """A subagent worker that completes immediately with *result*."""

    return result


# ---------------------------------------------------------------------------
# handle_message: IMMEDIATE vs QUEUED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_message_when_idle_returns_immediate_and_goes_working():
    """A message arriving while IDLE starts a turn immediately."""
    loop = AgentLoop()
    assert loop.state == LoopState.IDLE

    action = await loop.handle_message("hello")

    assert action == LoopAction.IMMEDIATE
    assert loop.state == LoopState.WORKING


@pytest.mark.asyncio
async def test_handle_message_when_working_is_queued_not_dropped():
    """A message arriving while WORKING is queued, never dropped."""
    loop = AgentLoop()

    await loop.handle_message("first turn")          # IDLE -> WORKING
    assert loop.state == LoopState.WORKING

    action = await loop.handle_message("mid-task message")
    assert action == LoopAction.QUEUED

    # The message is buffered, visible via status and the message_queue property.
    assert len(loop.message_queue) == 1
    assert loop.message_queue[0].content == "mid-task message"
    assert loop.state == LoopState.WORKING          # still working, not delivered


@pytest.mark.asyncio
async def test_multiple_messages_queued_in_arrival_order():
    """Several messages arriving during one turn are queued in FIFO order."""
    loop = AgentLoop()
    await loop.handle_message("turn")

    for i, text in enumerate(("m1", "m2", "m3")):
        action = await loop.handle_message(text, msg_id=f"msg-{i}")
        assert action == LoopAction.QUEUED

    contents = [m.content for m in loop.message_queue]
    assert contents == ["m1", "m2", "m3"]


# ---------------------------------------------------------------------------
# queue-not-drop: the queued message survives and is returned at the safe point
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queued_message_survives_safe_point_and_is_returned():
    """A queued message is not dropped when the turn ends.

    After reaching the safe point the message is returned to the caller for
    surfacing, and the delivered list is updated.
    """
    loop = AgentLoop()
    await loop.handle_message("long task")

    # Mid-task message is buffered.
    await loop.handle_message("help", msg_id="mid-1")
    assert len(loop.message_queue) == 1

    delivered = await loop.reach_safe_point()

    # The message was returned (not dropped).
    assert len(delivered) == 1
    assert delivered[0].content == "help"
    assert delivered[0].id == "mid-1"

    # Loop is idle again and the delivered log reflects the surface.
    assert loop.state == LoopState.IDLE
    assert loop.delivered[-1].content == "help"
    assert len(loop.message_queue) == 0


@pytest.mark.asyncio
async def test_empty_queue_at_safe_point_returns_empty_list():
    """A safe point with no queued messages returns an empty list."""
    loop = AgentLoop()
    await loop.handle_message("work")

    delivered = await loop.reach_safe_point()

    assert delivered == []
    assert loop.state == LoopState.IDLE


# ---------------------------------------------------------------------------
# safe-point-delivery: the message is NOT applied mid-step
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queued_message_is_not_applied_mid_step():
    """A message queued during WORKING is not acted on until the safe point.

    We model "applied" as: the message content would change the turn's
    behaviour. By asserting the loop is still WORKING and the queue is
    non-empty before reach_safe_point, and that reach_safe_point is the
    only path that yields the message, we prove it is not applied mid-step.
    """
    loop = AgentLoop()
    await loop.handle_message("original task")
    assert loop.state == LoopState.WORKING

    await loop.handle_message("redirect: do something else", is_redirect=True)

    # While working, the redirect sits in the queue -- it has NOT been
    # applied: the loop is still in the same turn.
    assert loop.state == LoopState.WORKING
    assert len(loop.message_queue) == 1
    assert loop.current_turn_id != "redirect: do something else"

    surfaced = await loop.reach_safe_point()

    # The redirect is delivered AT the safe point, not mid-step.
    assert len(surfaced) == 1
    assert surfaced[0].content == "redirect: do something else"
    assert surfaced[0].is_redirect is True
    assert loop.state == LoopState.IDLE


@pytest.mark.asyncio
async def test_safe_point_only_after_all_work_completes():
    """reaching_safe_point while a subagent runs still delivers the queue.

    The safe point is a turn boundary: messages are surfaced regardless of
    whether a subagent has finished, so the caller can decide to cancel.
    """
    loop = AgentLoop()
    await loop.handle_message("task")

    started = asyncio.Event()
    cancelled_flag = asyncio.Event()
    sub_id = await loop.spawn_subagent("heavy work", lambda p: _sleepy_worker(started, cancelled_flag))
    await started.wait()
    assert loop.get_subagent(sub_id).state == "running"

    await loop.handle_message("queued msg")
    assert len(loop.message_queue) == 1

    delivered = await loop.reach_safe_point()

    # The message is surfaced at the safe boundary.
    assert len(delivered) == 1
    assert delivered[0].content == "queued msg"

    # The subagent is still running (not cancelled -- no redirect this time).
    assert loop.get_subagent(sub_id).state == "running"

    # Clean up the lingering subagent.
    await loop.cancel_subagents()
    assert loop.get_subagent(sub_id).state == "cancelled"


# ---------------------------------------------------------------------------
# cancel-propagation: redirect cancels in-flight subagent work
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_subagents_cancels_running_task():
    """cancel_subagents cancels a running subagent and awaits its unwind."""
    loop = AgentLoop()
    started = asyncio.Event()
    cancelled_flag = asyncio.Event()

    sub_id = await loop.spawn_subagent(
        "heavy work", lambda p: _sleepy_worker(started, cancelled_flag),
    )
    await started.wait()

    assert loop.get_subagent(sub_id).state == "running"

    count = await loop.cancel_subagents(reason="manual cancel")
    assert count == 1

    # The CancelledError propagated into the worker.
    assert cancelled_flag.is_set()

    # The handle reflects the cancellation.
    handle = loop.get_subagent(sub_id)
    assert handle is not None
    assert handle.state == "cancelled"


@pytest.mark.asyncio
async def test_redirect_at_safe_point_cancels_inflight_subagent():
    """A redirect queued during WORKING cancels subagents at the safe boundary.

    This is the full cancel-propagation flow: spawn -> redirect queued ->
    reach_safe_point -> subagent cancelled -> redirect returned for re-dispatch.
    """
    loop = AgentLoop()
    await loop.handle_message("start a long build")

    started = asyncio.Event()
    cancelled_flag = asyncio.Event()

    sub_id = await loop.spawn_subagent(
        "building artifacts", lambda p: _sleepy_worker(started, cancelled_flag),
    )
    await started.wait()
    assert loop.get_subagent(sub_id).state == "running"

    # User sends a redirect while the subagent is busy.
    action = await loop.handle_message("abort and restart", is_redirect=True)
    assert action == LoopAction.QUEUED

    # The redirect is NOT applied mid-step.
    assert loop.state == LoopState.WORKING
    assert loop.has_pending_redirect() is True

    # Turn ends -> safe point. The redirect triggers subagent cancellation.
    delivered = await loop.reach_safe_point()
    await loop.await_subagent(sub_id)  # ensure task fully settled

    # Subagent was cancelled by the redirect.
    assert cancelled_flag.is_set()
    assert loop.get_subagent(sub_id).state == "cancelled"

    # The redirect is returned so the caller can start a new turn.
    assert len(delivered) == 1
    assert delivered[0].content == "abort and restart"
    assert delivered[0].is_redirect is True
    assert loop.state == LoopState.IDLE


@pytest.mark.asyncio
async def test_cancel_subagents_when_none_running_is_noop():
    """Cancelling with no in-flight subagents is a safe no-op."""
    loop = AgentLoop()
    count = await loop.cancel_subagents()
    assert count == 0
    assert loop.state == LoopState.IDLE


@pytest.mark.asyncio
async def test_cancel_subagents_only_cancels_running_not_completed():
    """Only running subagents are cancelled; completed ones are left alone."""
    loop = AgentLoop()

    sub_id = await loop.spawn_subagent("quick", lambda p: _quick_worker("ok"))
    await loop.await_subagent(sub_id)
    assert loop.get_subagent(sub_id).state == "completed"

    count = await loop.cancel_subagents()
    assert count == 0  # nothing was running


# ---------------------------------------------------------------------------
# Subagent lifecycle & progress streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subagent_completes_and_result_is_accessible():
    """A successful subagent populates its handle with the result."""
    loop = AgentLoop()

    sub_id = await loop.spawn_subagent("compute", lambda p: _quick_worker(42))
    result = await loop.await_subagent(sub_id)

    assert result == 42
    handle = loop.get_subagent(sub_id)
    assert handle.state == "completed"
    assert handle.result == 42


@pytest.mark.asyncio
async def test_subagent_progress_streamed_to_sink():
    """Progress dicts from the worker reach the loop's sink."""
    sink = _RecordingSink()
    loop = AgentLoop(sink=sink)

    async def worker(progress):
        progress({"kind": "reasoning", "content": "thinking..."})
        await asyncio.sleep(0)
        progress({"kind": "delta", "content": "partial"})
        return "done"

    sub_id = await loop.spawn_subagent("streamy task", worker)
    result = await loop.await_subagent(sub_id)

    assert result == "done"
    assert len(sink.received) == 2
    assert sink.received[0]["kind"] == "reasoning"
    assert sink.received[1]["kind"] == "delta"


@pytest.mark.asyncio
async def test_subagent_failure_sets_state_to_failed():
    """An exception in the worker sets state to 'failed' and records the error."""
    loop = AgentLoop()

    async def worker(progress):
        raise RuntimeError("kaboom")

    sub_id = await loop.spawn_subagent("doomed", worker)
    with pytest.raises(RuntimeError, match="kaboom"):
        await loop.await_subagent(sub_id)

    handle = loop.get_subagent(sub_id)
    assert handle.state == "failed"
    assert "kaboom" in (handle.error or "")


@pytest.mark.asyncio
async def test_await_subagent_raises_on_worker_exception():
    """A worker exception propagates through await_subagent, not swallowed."""
    loop = AgentLoop()

    async def worker(progress):
        raise RuntimeError("subagent boom")

    sub_id = await loop.spawn_subagent("doomed", worker)
    with pytest.raises(RuntimeError, match="subagent boom"):
        await loop.await_subagent(sub_id)

    handle = loop.get_subagent(sub_id)
    assert handle.state == "failed"
    assert "subagent boom" in (handle.error or "")


# ---------------------------------------------------------------------------
# Visibility: status() -- what is running / what is queued
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_reflects_state_subagents_and_queue():
    """status() surfaces loop state, running subagents, and queued messages."""
    loop = AgentLoop()
    await loop.handle_message("turn")

    started = asyncio.Event()
    cancelled_flag = asyncio.Event()
    sub_id = await loop.spawn_subagent(
        "background scan", lambda p: _sleepy_worker(started, cancelled_flag),
    )
    await started.wait()

    await loop.handle_message("queued user msg")

    st = loop.status()
    assert st["state"] == LoopState.WORKING.value
    assert st["queued_count"] == 1
    assert st["queued_messages"][0]["content"] == "queued user msg"
    assert len(st["subagents"]) == 1
    sa = st["subagents"][0]
    assert sa["id"] == sub_id
    assert sa["task"] == "background scan"
    assert sa["state"] == "running"
    assert st["current_turn_id"] is not None

    await loop.cancel_subagents()


@pytest.mark.asyncio
async def test_status_empty_when_loop_idle():
    """An idle loop reports no subagents and an empty queue."""
    loop = AgentLoop()
    st = loop.status()
    assert st["state"] == LoopState.IDLE.value
    assert st["queued_count"] == 0
    assert st["subagents"] == []
    assert st["queued_messages"] == []
    assert st["current_turn_id"] is None


# ---------------------------------------------------------------------------
# Long task in subagent while main loop stays responsive
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subagent_runs_while_main_loop_accepts_another_message():
    """A long task runs in a subagent; the main loop still accepts messages.

    The subagent works in the background (WORKING state) while a second
    message arrives and is queued -- the main loop is responsive (it does
    not block on the subagent). When the subagent finishes its result is
    available; queued messages are surfaced at the safe point.
    """
    loop = AgentLoop()
    await loop.handle_message("start")

    # Spawn a subagent that takes a moment.
    async def worker(progress):
        await asyncio.sleep(0.05)
        progress({"kind": "delta", "content": "subagent made progress"})
        return "subagent result"

    sub_id = await loop.spawn_subagent("long computation", worker)
    assert loop.get_subagent(sub_id).state == "running"

    # While the subagent runs, another message arrives -> queued (not blocked).
    action = await loop.handle_message("are you still there?")
    assert action == LoopAction.QUEUED
    assert len(loop.message_queue) == 1

    # Wait for the subagent to finish.
    result = await loop.await_subagent(sub_id)
    assert result == "subagent result"
    assert loop.get_subagent(sub_id).state == "completed"

    # Now reach the safe point; the queued message is delivered.
    delivered = await loop.reach_safe_point()
    assert len(delivered) == 1
    assert delivered[0].content == "are you still there?"


# ---------------------------------------------------------------------------
# get_subagent returns None for unknown ids
# ---------------------------------------------------------------------------

def test_get_subagent_unknown_returns_none():
    """Looking up an unknown subagent id returns None synchronously."""
    loop = AgentLoop()
    assert loop.get_subagent("nope") is None


# ---------------------------------------------------------------------------
# timeout-safe awaits: a timed-out poll must not cancel the subagent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_await_subagent_timeout_does_not_cancel_subagent():
    """A timeout on await_subagent does not cancel the underlying subagent task."""
    loop = AgentLoop()
    started = asyncio.Event()
    cancelled_flag = asyncio.Event()

    sub_id = await loop.spawn_subagent(
        "long work", lambda p: _sleepy_worker(started, cancelled_flag),
    )
    await started.wait()
    assert loop.get_subagent(sub_id).state == "running"

    with pytest.raises(asyncio.TimeoutError):
        await loop.await_subagent(sub_id, timeout=0.05)

    assert loop.get_subagent(sub_id).state == "running"
    assert not cancelled_flag.is_set()

    await loop.cancel_subagents()


@pytest.mark.asyncio
async def test_await_all_subagents_timeout_does_not_cancel_subagents():
    """await_all_subagents with a short timeout does not cancel running subagents."""
    loop = AgentLoop()
    started = asyncio.Event()
    cancelled_flag = asyncio.Event()

    sub_id = await loop.spawn_subagent(
        "long work", lambda p: _sleepy_worker(started, cancelled_flag),
    )
    await started.wait()
    assert loop.get_subagent(sub_id).state == "running"

    with pytest.raises(asyncio.TimeoutError):
        await loop.await_all_subagents(timeout=0.05)

    assert loop.get_subagent(sub_id).state == "running"
    assert not cancelled_flag.is_set()

    await loop.cancel_subagents()


# ---------------------------------------------------------------------------
# safe-point race: message arriving during cancel window is not dropped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_arriving_during_cancel_window_is_not_dropped_and_no_concurrent_turn():
    """A message that arrives while reach_safe_point is awaiting cancel_subagents
    is queued safely and delivered; no concurrent turn is started."""
    loop = AgentLoop()
    await loop.handle_message("start a long build")

    started = asyncio.Event()
    cancelled_flag = asyncio.Event()

    sub_id = await loop.spawn_subagent(
        "building artifacts", lambda p: _slow_cancel_worker(started, cancelled_flag),
    )
    await started.wait()
    assert loop.get_subagent(sub_id).state == "running"

    await loop.handle_message("abort and restart", is_redirect=True)
    assert loop.has_pending_redirect() is True

    async def _do_safe_point():
        return await loop.reach_safe_point()

    safe_point_task = asyncio.create_task(_do_safe_point())
    await asyncio.sleep(0.05)

    action = await loop.handle_message("message during cancel")
    assert action == LoopAction.QUEUED

    delivered = await safe_point_task

    assert any(m.content == "message during cancel" for m in delivered)
    assert loop.state == LoopState.IDLE
    assert loop.current_turn_id is None
