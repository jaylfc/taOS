"""Wake budget: OS-enforced per-agent / per-project / global daily wake limits.

The scheduler and heartbeat call ``can_wake`` before firing a scheduled check.
Mention wakes bypass the scheduled budget but are counted for Observatory
visibility. Daily consumption is persisted in ``data_dir/wake_budget.json``
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
_DEFAULT_MENTION_CAP = None


def _budget_path(data_dir: Path) -> Path:
    return data_dir / "wake_budget.json"


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {"daily": {}, "mentions": {}}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {"daily": {}, "mentions": {}}
        return data
    except (OSError, ValueError, TypeError):
        return {"daily": {}, "mentions": {}}


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
    try:
        n = int(v)
    except (TypeError, ValueError):
        return _DEFAULT_GLOBAL
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


def resolve_mention_cap(agent_id: str, config: Any) -> int | None:
    """Return the daily mention cap for an agent, or None when uncapped."""
    wb = getattr(config, "wake_budget", None) or {}
    per_agent_caps = wb.get("mention_cap") or {}
    if agent_id in per_agent_caps:
        v = per_agent_caps[agent_id]
        if v is None:
            return None
        return _coerce_budget(v)
    return _DEFAULT_MENTION_CAP


def record_scheduled_wake(data_dir: Path, agent_id: str, project_id: str | None) -> None:
    path = _budget_path(data_dir)
    state = _read_state(path)
    today = _today()
    key = f"{agent_id}:{project_id or 'global'}"
    daily = state.setdefault("daily", {})
    agent_daily = daily.setdefault(key, {})
    agent_daily[today] = int(agent_daily.get(today, 0)) + 1
    _write_state(path, state)


def record_mention_wake(data_dir: Path, agent_id: str) -> None:
    path = _budget_path(data_dir)
    state = _read_state(path)
    today = _today()
    mentions = state.setdefault("mentions", {})
    agent_mentions = mentions.setdefault(agent_id, {})
    agent_mentions[today] = int(agent_mentions.get(today, 0)) + 1
    _write_state(path, state)


def get_consumption(data_dir: Path, agent_id: str, project_id: str | None) -> dict:
    path = _budget_path(data_dir)
    state = _read_state(path)
    today = _today()
    key = f"{agent_id}:{project_id or 'global'}"
    scheduled = int(state.get("daily", {}).get(key, {}).get(today, 0))
    mention = int(state.get("mentions", {}).get(agent_id, {}).get(today, 0))
    return {"scheduled": scheduled, "mention": mention, "date": today}


def can_wake(
    data_dir: Path,
    agent_id: str,
    agent_name: str,
    project_id: str | None,
    config: Any,
    wake_type: str = "scheduled",
) -> bool:
    """Return True when the agent may be woken for *wake_type*.

    Scheduled wakes are blocked when the resolved budget is exhausted.
    Mention wakes always pass the gate (they bypass the schedule) but are
    still recorded for observability.
    """
    if wake_type == "mention":
        return True
    budget = resolve_budget(agent_id, project_id, config)
    if budget <= 0:
        return False
    consumption = get_consumption(data_dir, agent_id, project_id)
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


def get_fleet_wake_info(data_dir: Path, config: Any, project_store: Any = None) -> list[dict]:
    """Return wake info for every active agent in config."""
    rows: list[dict] = []
    agents = getattr(config, "agents", None) or []
    for agent in agents:
        if agent.get("status") != "active":
            continue
        agent_id = agent.get("id") or agent.get("name") or ""
        if not agent_id:
            continue
        budget = resolve_budget(agent_id, None, config)
        consumption = get_consumption(data_dir, agent_id, None)
        remaining = max(0, budget - consumption["scheduled"])
        rows.append({
            "agent_id": agent_id,
            "agent_name": agent.get("name", agent_id),
            "budget": budget,
            "consumed": consumption["scheduled"],
            "remaining": remaining,
            "mention_count": consumption["mention"],
            "next_wake_epoch": get_next_scheduled_wake(data_dir, agent_id, None, config),
        })
    return rows
