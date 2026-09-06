"""Scheduled task executor: cron tick loop for TaskScheduler entries.

Mirrors tinyagentos/projects/routine_runner.py's routine_tick_loop: a plain
`while True` + try/except-per-iteration + sleep, registered in
app.state._background_tasks so the existing bounded `cancel_and_wait`
shutdown path cancels it alongside every other loop.

SECURITY: the `command` field on a scheduled task is an arbitrary string (UI
presets include things like `curl ...` and `qmd embed`). It is NEVER
shell-executed. Dispatch is limited to a hardcoded allow-list of internal
handlers (`_SAFE_COMMANDS`) keyed by exact command string. Any command not in
the allow-list is logged at debug level and skipped.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

from croniter import croniter

from tinyagentos.data_snapshot import snapshot_data_dir

logger = logging.getLogger(__name__)

TICK_INTERVAL = 60  # seconds between due-task sweeps

_AUTO_BACKUP_KEEP = 7  # how many auto-* snapshots to retain


async def _run_backup(app_state) -> None:
    """Snapshot the data dir with the "auto" prefix, then prune old auto
    snapshots down to the most recent _AUTO_BACKUP_KEEP."""
    data_dir = app_state.data_dir
    backup_path = snapshot_data_dir(data_dir, prefix="auto")
    logger.info("scheduled backup: created %s", backup_path)

    backups_root = data_dir / "data-backups"
    auto_backups = sorted(
        (p for p in backups_root.glob("auto-*") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in auto_backups[_AUTO_BACKUP_KEEP:]:
        try:
            shutil.rmtree(stale)
        except OSError as exc:
            logger.warning("scheduled backup: failed to prune %s: %s", stale, exc)


# Hardcoded allow-list: only these exact command strings are ever dispatched.
# Anything else is logged and skipped; stored command strings are never
# shell-executed.
_SAFE_COMMANDS = {
    "create_backup": _run_backup,
}


async def run_due_once(app_state, now: float) -> int:
    """Sweep enabled scheduled tasks once and fire each one that is due.

    Returns the number of commands actually dispatched. Failure-isolated per
    task: one bad task is logged and skipped, never aborting the sweep.
    """
    scheduler = app_state.scheduler
    dispatched = 0
    for task in await scheduler.list_enabled():
        try:
            schedule = task["schedule"]
            if not croniter.is_valid(schedule):
                continue
            base = task["last_run"] if task["last_run"] else task["created_at"]
            due_at = croniter(schedule, base).get_next(float)
            if due_at > now:
                continue
            next_run = int(croniter(schedule, now).get_next(float))
            claimed = await scheduler.claim_run(
                task["id"], task["last_run"], int(now), next_run
            )
            if not claimed:
                # Another tick already fired this due instant.
                continue
            handler = _SAFE_COMMANDS.get(task["command"])
            if handler is None:
                logger.debug(
                    "scheduled task %s: unhandled command %r, skipping",
                    task["id"], task["command"],
                )
                continue
            await handler(app_state)
            dispatched += 1
        except Exception:
            logger.exception(
                "scheduled task tick: fire failed for task %s", task.get("id")
            )
    return dispatched


async def scheduled_task_tick_loop(app_state) -> None:
    """Sweep due scheduled tasks every TICK_INTERVAL seconds and dispatch
    each one via the allow-listed handler for its command."""
    while True:
        try:
            await run_due_once(app_state, time.time())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduled task tick: sweep crashed")
        await asyncio.sleep(TICK_INTERVAL)
