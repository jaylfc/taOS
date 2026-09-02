"""Wake budget: OS-enforced per-agent / per-project / global daily wake limits.

The scheduler and heartbeat call ``can_wake`` before firing a scheduled check.
Daily consumption is persisted in ``data_dir/wake_budget.json``
and rolls over automatically by date.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_GLOBAL = 2


class WakeBudgetStateError(Exception):
    """Raised when ``wake_budget.json`` exists but cannot be read or parsed.

    This is distinct from a *missing* file (which is a fresh state). A
    damaged file must fail closed -- see :func:`can_wake` -- rather than
    silently restoring a full budget to every agent.
    """


def _budget_path(data_dir: Path) -> Path:
    return data_dir / "wake_budget.json"


def _read_state(path: Path) -> dict:
    """Read and parse the wake-budget state file.

    A *missing* file is a fresh state: returns ``{"daily": {}}``.
    A *present-but-damaged* file raises :class:`WakeBudgetStateError` so the
    caller can fail closed instead of silently reporting zero consumption.
    """
    if not path.exists():
        return {"daily": {}}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError, TypeError) as e:
        raise WakeBudgetStateError(
            f"wake_budget.json exists but is unreadable/damaged: {e}"
        ) from e
    if not isinstance(data, dict):
        raise WakeBudgetStateError(
            f"wake_budget.json root is {type(data).__name__}, expected a JSON object"
        )
    return data


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(state, sort_keys=True))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _coerce_budget(v: Any) -> int:
    n = int(v)
    return max(0, n)


def resolve_budget(agent_id: str, project_id: str | None, config: Any) -> int:
    """Resolve the scheduled wake budget for an agent.

    Cascade: per-project > per-agent > global_default.
    """
    wb = getattr(config, "wake_budget", None) or {}
    if project_id:
        per_project = wb.get("per_project") or {}
        if project_id in per_project:
            return _coerce_budget(per_project[project_id])
    per_agent = wb.get("per_agent") or {}
    if agent_id in per_agent:
        return _coerce_budget(per_agent[agent_id])
    return _coerce_budget(wb.get("global_default", _DEFAULT_GLOBAL))


def record_scheduled_wake(data_dir: Path, agent_id: str, project_id: str | None) -> None:
    """Record one scheduled wake for today.

    The heartbeat loop is the single writer for this file. Do not call
    concurrently from multiple processes without an external lock.
    """
    path = _budget_path(data_dir)
    state = _read_state(path)
    today = _today()
    key = f"{agent_id}:{project_id or 'global'}"
    daily = state.setdefault("daily", {})
    agent_daily = daily.setdefault(key, {})
    agent_daily[today] = int(agent_daily.get(today, 0)) + 1
    for k in list(agent_daily):
        if k != today:
            del agent_daily[k]
    _write_state(path, state)


def get_consumption(data_dir: Path, agent_id: str, project_id: str | None) -> dict:
    path = _budget_path(data_dir)
    state = _read_state(path)
    today = _today()
    key = f"{agent_id}:{project_id or 'global'}"
    scheduled = int(state.get("daily", {}).get(key, {}).get(today, 0))
    return {"scheduled": scheduled, "date": today}


def can_wake(
    data_dir: Path,
    agent_id: str,
    agent_name: str,
    project_id: str | None,
    config: Any,
) -> bool:
    """Return True when the agent may be woken for a scheduled check.

    Fails closed (returns False) when the state file is damaged: a corrupted
    ``wake_budget.json`` must not silently restore a full budget and let
    enforcement cease fleet-wide.
    """
    budget = resolve_budget(agent_id, project_id, config)
    if budget <= 0:
        return False
    try:
        consumption = get_consumption(data_dir, agent_id, project_id)
    except WakeBudgetStateError:
        logger.exception(
            "wake budget state damaged, refusing wake for agent %s", agent_id
        )
        return False
    return consumption["scheduled"] < budget


def get_next_scheduled_wake(
    data_dir: Path,
    agent_id: str,
    project_id: str | None,
    config: Any,
) -> float | None:
    """Return the epoch of the next scheduled wake, or None if exhausted."""
    budget = resolve_budget(agent_id, project_id, config)
    if budget <= 0:
        return None
    consumption = get_consumption(data_dir, agent_id, project_id)
    remaining = budget - consumption["scheduled"]
    if remaining <= 0:
        return None
    now = time.time()
    seconds_left_today = max(1, 86400 - (now % 86400))
    return now + seconds_left_today / remaining


async def get_fleet_wake_info(data_dir: Path, config: Any, project_task_store: Any = None) -> list[dict]:
    """Return wake info for every running agent in config."""
    rows: list[dict] = []
    agents = getattr(config, "agents", None) or []
    for agent in agents:
        if agent.get("status") != "running":
            continue
        agent_id = agent.get("id") or agent.get("name") or ""
        if not agent_id:
            continue
        project_id = None
        if project_task_store is not None:
            try:
                held = await project_task_store.held_task(agent_id)
                if held is not None:
                    task = await project_task_store.get_task(held)
                    if task is not None:
                        project_id = task.get("project_id")
                else:
                    ready = await project_task_store.list_ready_tasks_for_assignee(agent_id)
                    if ready:
                        project_id = ready[0].get("project_id")
            except Exception:
                logger.warning(
                    "wake budget: project_task_store lookup failed for agent %s",
                    agent_id, exc_info=True,
                )
        budget = resolve_budget(agent_id, project_id, config)
        try:
            consumption = get_consumption(data_dir, agent_id, project_id)
        except WakeBudgetStateError as exc:
            logger.warning(
                "wake budget: skipping fleet row for %s due to damaged state: %s",
                agent_id, exc,
            )
            rows.append({
                "agent_id": agent_id,
                "agent_name": agent.get("name", agent_id),
                "budget": budget,
                "consumed": 0,
                "remaining": 0,
                "next_wake_epoch": None,
                "state": "damaged",
            })
            continue
        try:
            next_wake_epoch = get_next_scheduled_wake(data_dir, agent_id, project_id, config)
        except WakeBudgetStateError as exc:
            logger.warning(
                "wake budget: skipping fleet row for %s due to damaged state: %s",
                agent_id, exc,
            )
            rows.append({
                "agent_id": agent_id,
                "agent_name": agent.get("name", agent_id),
                "budget": budget,
                "consumed": consumption["scheduled"],
                "remaining": max(0, budget - consumption["scheduled"]),
                "next_wake_epoch": None,
                "state": "damaged",
            })
            continue
        remaining = max(0, budget - consumption["scheduled"])
        rows.append({
            "agent_id": agent_id,
            "agent_name": agent.get("name", agent_id),
            "budget": budget,
            "consumed": consumption["scheduled"],
            "remaining": remaining,
            "next_wake_epoch": next_wake_epoch,
        })
    return rows
