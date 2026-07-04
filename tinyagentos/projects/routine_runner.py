"""Routine executor: cron tick loop + the shared fire helper.

A routine auto-creates a task on its project board when it fires (on a cron
schedule, via webhook, or via manual/API trigger) and best-effort wakes the
assignee agent. Task creation always happens; the wake (bridge_sessions
enqueue + a2a system message) is best-effort — the assignee may be offline
and will pick the task up later.

The tick loop mirrors the other lifespan-owned background loops in app.py
(e.g. `_ephemeral_sweep_loop`): a plain `while True` + try/except-per-iteration
+ sleep, registered in `app.state._background_tasks` so the existing bounded
`cancel_and_wait` shutdown path cancels it alongside every other loop — no
separate start/stop lifecycle needed.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

TICK_INTERVAL = 60  # seconds between due-routine sweeps


def _resolve_assignee_agent(config, assignee_id: str | None) -> dict | None:
    """Resolve a routine's assignee_id (a project member_id — an agent's
    config id or name) to its config agent record.

    Mirrors the id/name lookup used by tinyagentos/projects/a2a.py's
    _resolve_member_names and the reverse direction in
    beads_bridge.BeadsBridge._resolve_assignee. Returns None when config is
    unavailable or assignee_id doesn't match any configured agent — callers
    must treat the wake as best-effort in that case.
    """
    if not assignee_id or config is None:
        return None
    for agent in getattr(config, "agents", None) or []:
        if agent.get("id") == assignee_id or agent.get("name") == assignee_id:
            return agent
    return None


async def _wake_and_announce(app_state, routine: dict, task: dict) -> None:
    """Best-effort: enqueue a user_message to a running assignee agent and
    post a system message into the project's a2a channel. Never raises —
    callers already wrap this in a try/except, but each sub-step also
    degrades independently so a bridge failure doesn't skip the chat post."""
    project_id = routine["project_id"]
    config = getattr(app_state, "config", None)
    bridge_sessions = getattr(app_state, "bridge_sessions", None)

    agent = _resolve_assignee_agent(config, routine.get("assignee_id"))
    if agent is not None and agent.get("status") == "running" and bridge_sessions is not None:
        try:
            await bridge_sessions.enqueue_user_message(
                agent["name"],
                {
                    "id": task["id"],
                    "trace_id": task["id"],
                    "channel_id": None,
                    "from": "routine",
                    "text": f"Routine \"{routine['title']}\" fired — created task {task['id']}: {task['title']}",
                    "author_type": "system",
                    "hops_since_user": 0,
                },
            )
        except Exception:
            logger.warning(
                "routine %s: wake enqueue failed for agent %s",
                routine["id"], agent.get("name"), exc_info=True,
            )

    chat_channels = getattr(app_state, "chat_channels", None)
    chat_messages = getattr(app_state, "chat_messages", None)
    project_store = getattr(app_state, "project_store", None)
    if chat_channels is not None and chat_messages is not None and project_store is not None:
        try:
            from tinyagentos.projects.a2a import ensure_a2a_channel
            channel = await ensure_a2a_channel(
                chat_channels, project_store, project_id, config=config
            )
            await chat_messages.send_message(
                channel_id=channel["id"],
                author_id="routine",
                author_type="system",
                content=f"Routine \"{routine['title']}\" fired — created task {task['id']}: {task['title']}",
                content_type="system",
                state="complete",
            )
        except Exception:
            logger.warning(
                "routine %s: a2a announce failed", routine["id"], exc_info=True,
            )


async def _create_task_and_wake(app_state, routine: dict) -> dict:
    """Create the board task for *routine* and best-effort wake the assignee.

    Does NOT advance the routine's schedule — the caller is responsible for
    that (record_fire for manual/webhook, claim_due for the scheduler tick).
    Task creation always happens; wake is best-effort. Returns the task dict.
    """
    task = await app_state.project_task_store.create_task(
        project_id=routine["project_id"],
        title=routine["title"],
        created_by=f"routine:{routine['id']}",
        body=routine.get("body_template") or "",
        assignee_id=routine.get("assignee_id"),
    )
    try:
        await _wake_and_announce(app_state, routine, task)
    except Exception:
        logger.warning("routine %s: wake/announce crashed", routine["id"], exc_info=True)
    return task


async def fire_routine(app_state, routine: dict) -> dict:
    """Manual/API and webhook entry point: create a task, advance the schedule,
    then best-effort wake the assignee. Task creation always happens regardless
    of what happens afterwards. Returns the created task dict.

    The scheduler's tick loop does NOT call this — it claims the schedule
    atomically first (see routine_tick_loop) to avoid double-firing.
    """
    task = await _create_task_and_wake(app_state, routine)
    await app_state.routine_store.record_fire(routine["id"], time.time())
    return task


async def routine_tick_loop(app_state) -> None:
    """Sweep due cron routines every TICK_INTERVAL seconds and fire each one.

    Each due routine is claimed atomically (claim_due advances its schedule
    under a next_fire guard) BEFORE the task is created, so a concurrent or
    restarted tick that selected the same routine loses the claim and skips it —
    the routine fires exactly once per due instant. Failure-isolated per
    iteration and per routine: one bad routine (e.g. a stale project) is logged
    and skipped, never aborting the loop.
    """
    routine_store = app_state.routine_store
    while True:
        try:
            now = time.time()
            due = await routine_store.list_due(now)
            for routine in due:
                try:
                    claimed = await routine_store.claim_due(
                        routine["id"], routine["next_fire"], now
                    )
                    if not claimed:
                        # Another tick already fired this due instant.
                        continue
                    await _create_task_and_wake(app_state, routine)
                except Exception:
                    logger.exception(
                        "routine tick: fire failed for routine %s", routine.get("id")
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("routine tick: sweep iteration crashed")
        await asyncio.sleep(TICK_INTERVAL)
