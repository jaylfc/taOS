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
    Every resolution is logged so cost is attributable.
    """
    wb = getattr(config, "wake_budget", None) or {}
    if project_id:
        per_project = wb.get("per_project") or {}
        if project_id in per_project:
            budget = _coerce_budget(per_project[project_id])
            logger.debug(
                "wake_budget resolve: agent=%s project=%s -> %d (per_project)",
                agent_id, project_id, budget,
            )
            return budget
    per_agent = wb.get("per_agent") or {}
    if agent_id in per_agent:
        budget = _coerce_budget(per_agent[agent_id])
        logger.debug(
            "wake_budget resolve: agent=%s project=%s -> %d (per_agent)",
            agent_id, project_id, budget,
        )
        return budget
    budget = _coerce_budget(wb.get("global_default", _DEFAULT_GLOBAL))
    logger.debug(
        "wake_budget resolve: agent=%s project=%s -> %d (global_default)",
        agent_id, project_id, budget,
    )
    return budget


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


def _next_wake_epoch(budget: int, consumed: int) -> float | None:
    """Spread remaining budget evenly over the rest of today; None if exhausted."""
    if budget <= 0:
        return None
    remaining = budget - consumed
    if remaining <= 0:
        return None
    now = time.time()
    seconds_left_today = max(1, 86400 - (now % 86400))
    return now + seconds_left_today / remaining


def get_next_scheduled_wake(
    data_dir: Path,
    agent_id: str,
    project_id: str | None,
    config: Any,
) -> float | None:
    """Return the epoch of the next scheduled wake, or None if exhausted."""
    budget = resolve_budget(agent_id, project_id, config)
    consumption = get_consumption(data_dir, agent_id, project_id)
    return _next_wake_epoch(budget, consumption["scheduled"])


def get_agent_wake_info(data_dir: Path, agent_id: str, config: Any) -> dict:
    """Wake budget summary for a single agent, aggregated across all projects.

    The heartbeat charges per ``(agent, task.project_id)`` and the agent dict
    never carries a ``project_id`` in production, so a single-project lookup
    would miss every charge. This sums consumption across every
    ``f"{agent_id}:*"`` key so the read matches what was written. The budget is
    resolved to the most restrictive applicable ``per_project`` override across
    the agent's recorded projects, falling back to the per-agent / global
    default when no consumption is on record yet.
    """
    path = _budget_path(data_dir)
    state = _read_state(path)
    today = _today()
    prefix = f"{agent_id}:"
    total_consumed = 0
    projects: set[str] = set()
    for key, daily in state.get("daily", {}).items():
        if key.startswith(prefix):
            proj = key[len(prefix):]
            if proj != "global":
                projects.add(proj)
            total_consumed += int(daily.get(today, 0))
    if projects:
        budget = min(resolve_budget(agent_id, p, config) for p in projects)
    else:
        budget = resolve_budget(agent_id, None, config)
    remaining = max(0, budget - total_consumed)
    next_wake = _next_wake_epoch(budget, total_consumed)
    return {
        "budget": budget,
        "consumed": total_consumed,
        "remaining": remaining,
        "next_wake_epoch": next_wake,
        "date": today,
    }


def get_fleet_wake_info(data_dir: Path, config: Any, project_store: Any = None) -> list[dict]:
    """Return wake info for every running agent in config.

    Consumption is aggregated across all of an agent's project keys so the
    reported total matches what the heartbeat actually charged per
    (agent, task.project_id). Budget is resolved to the most restrictive
    applicable per-project override across those projects.
    """
    rows: list[dict] = []
    agents = getattr(config, "agents", None) or []
    for agent in agents:
        if agent.get("status") != "running":
            continue
        agent_id = agent.get("id") or agent.get("name") or ""
        if not agent_id:
            continue
        info = get_agent_wake_info(data_dir, agent_id, config)
        rows.append({
            "agent_id": agent_id,
            "agent_name": agent.get("name", agent_id),
            "budget": info["budget"],
            "consumed": info["consumed"],
            "remaining": info["remaining"],
            "next_wake_epoch": info["next_wake_epoch"],
        })
    return rows
