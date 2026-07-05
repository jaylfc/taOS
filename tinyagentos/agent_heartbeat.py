"""Agent heartbeat: on a periodic tick, wake idle running agents with their
next ready task plus that task's relational context, so agents pull and act
on their queue autonomously rather than waiting to be prompted.

"Act" means notify, not drive: this best-effort enqueues a system message
onto the agent's own bridge session and posts to the project's a2a channel --
the exact mechanism tinyagentos.projects.routine_runner uses to wake an
assignee. It never drives an ACP/LLM turn directly and never deploys or execs
a non-running agent (status != "running" is skipped entirely).

The tick loop mirrors routine_tick_loop and the other lifespan-owned
background loops in app.py (e.g. _ephemeral_sweep_loop): a plain `while True`
+ try/except-per-iteration + sleep, registered in app.state._background_tasks
so the existing bounded cancel_and_wait shutdown path cancels it alongside
every other loop -- no separate start/stop lifecycle needed.

Opt-in: gated behind config.server["agent_heartbeat_enabled"] (default
False), re-read every tick from app_state.config so it can be toggled via the
existing PUT /api/config endpoint without a restart.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 60  # seconds between agent queue sweeps
REWAKE_COOLDOWN = 300  # seconds before the same (agent, task) may be re-woken


def _heartbeat_enabled(app_state) -> bool:
    config = getattr(app_state, "config", None)
    server = getattr(config, "server", None) or {}
    return bool(server.get("agent_heartbeat_enabled", False))


def _debounce_map(app_state) -> dict:
    """The in-memory {agent_id: (task_id, last_wake_ts)} dedup, created lazily
    on app_state so it survives across ticks within a process without a new
    persistent store."""
    if not hasattr(app_state, "_heartbeat_last_wake"):
        app_state._heartbeat_last_wake = {}
    return app_state._heartbeat_last_wake


def _should_wake(debounce: dict, agent_id: str, task_id: str, now: float) -> bool:
    last = debounce.get(agent_id)
    if last is not None and last[0] == task_id and now - last[1] < REWAKE_COOLDOWN:
        return False
    return True


async def _wake_agent_with_task(app_state, agent: dict, task: dict) -> bool:
    """Best-effort wake: enqueue a user_message to the agent's bridge session
    and post a system message into the project's a2a channel. Mirrors
    tinyagentos.projects.routine_runner._wake_and_announce -- each sub-step
    degrades independently so one failure doesn't skip the other.

    Returns True only when the primary enqueue reached the agent's queue, so
    the caller debounces successful wakes and retries failed ones next tick
    instead of silencing the agent for the whole cooldown."""
    project_task_store = app_state.project_task_store
    config = getattr(app_state, "config", None)

    try:
        context = await project_task_store.get_task_context(task["id"])
    except Exception:
        logger.warning(
            "heartbeat: get_task_context failed for task %s", task["id"], exc_info=True,
        )
        context = None

    from tinyagentos.projects.beads_format import format_why
    why = None
    if context is not None:
        why = format_why(context["project"], context["ancestry"])

    text = f"Heartbeat: you have a ready task {task['id']}: {task['title']}"
    if why:
        text = f"{text}\n{why}"

    enqueued = False
    bridge_sessions = getattr(app_state, "bridge_sessions", None)
    if bridge_sessions is not None:
        try:
            await bridge_sessions.enqueue_user_message(
                agent["name"],
                {
                    # A fresh message id: reusing the task id would collide in
                    # any consumer that keys by message id (a task can be
                    # announced more than once).
                    "id": uuid.uuid4().hex,
                    "trace_id": f"heartbeat-{task['id']}",
                    "channel_id": None,
                    "from": "heartbeat",
                    "text": text,
                    "author_type": "system",
                    "hops_since_user": 0,
                },
            )
            enqueued = True
        except Exception:
            logger.warning(
                "heartbeat: wake enqueue failed for agent %s", agent.get("name"), exc_info=True,
            )

    chat_channels = getattr(app_state, "chat_channels", None)
    chat_messages = getattr(app_state, "chat_messages", None)
    project_store = getattr(app_state, "project_store", None)
    if chat_channels is not None and chat_messages is not None and project_store is not None:
        try:
            from tinyagentos.projects.a2a import ensure_a2a_channel
            channel = await ensure_a2a_channel(
                chat_channels, project_store, task["project_id"], config=config
            )
            await chat_messages.send_message(
                channel_id=channel["id"],
                author_id="heartbeat",
                author_type="system",
                content=text,
                content_type="system",
                state="complete",
            )
        except Exception:
            logger.warning(
                "heartbeat: a2a announce failed for task %s", task["id"], exc_info=True,
            )

    return enqueued


async def _heartbeat_tick(app_state) -> None:
    """One sweep over app_state.config.agents: wake each idle running agent's
    next ready task, debounced per (agent, task) within REWAKE_COOLDOWN.
    Failure-isolated per agent so one bad agent never breaks the tick."""
    if not _heartbeat_enabled(app_state):
        return

    config = getattr(app_state, "config", None)
    project_task_store = app_state.project_task_store
    debounce = _debounce_map(app_state)
    now = time.time()

    for agent in getattr(config, "agents", None) or []:
        if agent.get("status") != "running":
            continue
        agent_id = agent.get("id")
        if not agent_id:
            continue
        try:
            if await project_task_store.held_task(agent_id) is not None:
                continue
            ready = await project_task_store.list_ready_tasks_for_assignee(agent_id)
            if not ready:
                continue
            task = ready[0]
            if not _should_wake(debounce, agent_id, task["id"], now):
                continue
            # Debounce only a wake that actually reached the agent's queue; a
            # failed enqueue retries next tick instead of silencing the agent
            # for the whole cooldown.
            if await _wake_agent_with_task(app_state, agent, task):
                debounce[agent_id] = (task["id"], now)
        except Exception:
            logger.exception("heartbeat: tick failed for agent %s", agent.get("name"))


async def agent_heartbeat_loop(app_state, interval: float = HEARTBEAT_INTERVAL) -> None:
    """Sweep running agents every *interval* seconds and wake each idle one
    with its next ready task. No-op (cheap) while
    config.server["agent_heartbeat_enabled"] is falsy, re-checked every tick
    so the setting can be toggled without a restart."""
    while True:
        try:
            await _heartbeat_tick(app_state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("heartbeat: sweep iteration crashed")
        await asyncio.sleep(interval)
