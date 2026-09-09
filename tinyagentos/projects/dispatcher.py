from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from tinyagentos.agent_registry_store import agent_slug_or_fallback
from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

DISPATCHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatcher_config (
    user_id TEXT NOT NULL PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    boards TEXT NOT NULL DEFAULT '[]',
    eligible_agents TEXT NOT NULL DEFAULT '[]',
    max_concurrent_per_agent INTEGER NOT NULL DEFAULT 1,
    poll_seconds INTEGER NOT NULL DEFAULT 30
);
"""

_DEFAULT_CONFIG = {
    "enabled": False,
    "boards": [],
    "eligible_agents": [],
    "max_concurrent_per_agent": 1,
    "poll_seconds": 30,
}

# canonical_id shape: {slug}-{YYYYMMDD}-{HHMMSS}[-{2hex}]
_CANONICAL_ID_RE = re.compile(r"^(.+)-(\d{8}-\d{6})(-[0-9a-f]{2})?$")


class DispatcherStore(BaseStore):
    SCHEMA = DISPATCHER_SCHEMA

    async def get_config(self, user_id: str) -> dict:
        if self._db is None:
            raise RuntimeError("DispatcherStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM dispatcher_config WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return dict(_DEFAULT_CONFIG)
        keys = [d[0] for d in cursor.description]
        d = dict(zip(keys, row))
        d["boards"] = json.loads(d.get("boards") or "[]")
        d["eligible_agents"] = json.loads(d.get("eligible_agents") or "[]")
        d["enabled"] = bool(d.get("enabled", 0))
        return d

    async def update_config(self, user_id: str, **kwargs) -> dict:
        if self._db is None:
            raise RuntimeError("DispatcherStore not initialised")
        current = await self.get_config(user_id)
        current.update(kwargs)
        boards = json.dumps(current.get("boards") or [])
        eligible = json.dumps(current.get("eligible_agents") or [])
        enabled = 1 if current.get("enabled") else 0
        max_cc = int(current.get("max_concurrent_per_agent") or 1)
        poll = int(current.get("poll_seconds") or 30)
        await self._db.execute(
            """INSERT INTO dispatcher_config (user_id, enabled, boards, eligible_agents, max_concurrent_per_agent, poll_seconds)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 enabled = excluded.enabled,
                 boards = excluded.boards,
                 eligible_agents = excluded.eligible_agents,
                 max_concurrent_per_agent = excluded.max_concurrent_per_agent,
                 poll_seconds = excluded.poll_seconds""",
            (user_id, enabled, boards, eligible, max_cc, poll),
        )
        await self._db.commit()
        return await self.get_config(user_id)


class DispatcherService:
    def __init__(self, app_state, dispatcher_store, project_task_store, agent_grants_store, agent_registry_store, project_store):
        self._app_state = app_state
        self._dispatcher_store = dispatcher_store
        self._task_store = project_task_store
        self._grants_store = agent_grants_store
        self._registry_store = agent_registry_store
        self._project_store = project_store

    async def get_config(self, user_id: str) -> dict:
        return await self._dispatcher_store.get_config(user_id)

    async def update_config(self, user_id: str, **kwargs) -> dict:
        return await self._dispatcher_store.update_config(user_id, **kwargs)

    async def _resolve_agent_by_canonical_id(self, canonical_id: str) -> dict | None:
        """Map a canonical_id to its config agent dict by slug match."""
        registry = await self._registry_store.get(canonical_id)
        if registry is None:
            return None
        display_name = registry.get("display_name", "")
        slug = agent_slug_or_fallback(display_name)
        config = getattr(self._app_state, "config", None)
        if config is None:
            return None
        for agent in getattr(config, "agents", None) or []:
            if agent.get("name") == slug:
                return agent
        return None

    async def _resolve_canonical_id(self, agent_id: str) -> str | None:
        """Resolve any agent identifier (config id/name or canonical_id) to canonical_id."""
        config = getattr(self._app_state, "config", None)
        if config is not None:
            for agent in getattr(config, "agents", None) or []:
                if agent.get("id") == agent_id or agent.get("name") == agent_id:
                    # Look up registry record for this config agent
                    registry = await self._registry_store.get_by_slug(agent.get("name", ""))
                    if registry:
                        return registry.get("canonical_id")
                    return None
        # Maybe it's already a canonical_id
        if await self._registry_store.get(agent_id) is not None:
            return agent_id
        return None

    async def _agent_has_project_tasks_grant(self, canonical_id: str, project_id: str) -> bool:
        if self._grants_store is None:
            return False
        try:
            grants = await self._grants_store.list_grants(canonical_id)
        except RuntimeError:
            return False
        now = datetime.now(timezone.utc)
        for g in grants:
            if g.get("scope") != "project_tasks":
                continue
            if g.get("project_id") != project_id:
                continue
            expires_at = g.get("expires_at")
            if expires_at is not None:
                try:
                    exp = datetime.fromisoformat(str(expires_at))
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp <= now:
                        continue
                except (ValueError, TypeError):
                    continue
            return True
        return False

    async def _get_agent_claimed_count(self, agent_id: str) -> int:
        async with self._task_store._read(
            "SELECT COUNT(*) FROM project_tasks WHERE claimed_by = ? AND status = 'claimed'",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def _is_task_blocked(self, task: dict) -> bool:
        labels = task.get("labels") or []
        for lbl in labels:
            if not lbl.startswith("blocked-on:"):
                continue
            dep_id = lbl[len("blocked-on:"):]
            dep = await self._task_store.get_task(dep_id)
            if dep is None:
                continue
            if dep.get("status") not in ("closed", "cancelled"):
                return True
        return False

    async def _get_candidates_for_user(self, user_id: str) -> list[tuple[dict, str]]:
        cfg = await self.get_config(user_id)
        if not cfg.get("enabled"):
            return []
        boards = list(cfg.get("boards") or [])
        eligible_entries = list(cfg.get("eligible_agents") or [])
        max_cc = int(cfg.get("max_concurrent_per_agent") or 1)

        if not boards:
            projects = await self._project_store.list_for_user(user_id, status="active")
            boards = [p["id"] for p in projects]

        if not boards or not eligible_entries:
            return []

        # Resolve eligible entries to canonical_ids
        eligible_canonical_ids = []
        for entry in eligible_entries:
            cid = await self._resolve_canonical_id(entry)
            if cid is not None:
                eligible_canonical_ids.append(cid)

        if not eligible_canonical_ids:
            return []

        # Filter to agents with project_tasks grant on at least one enabled board
        eligible_set = set()
        for cid in eligible_canonical_ids:
            for board_id in boards:
                if await self._agent_has_project_tasks_grant(cid, board_id):
                    eligible_set.add(cid)
                    break

        if not eligible_set:
            return []

        # Gather claimable tasks across all enabled boards
        placeholders = ",".join("?" * len(boards))
        params = list(boards)

        sql = f"""
            SELECT * FROM project_tasks
            WHERE project_id IN ({placeholders})
              AND status = 'open'
              AND claimed_by IS NULL
              AND assignee_id IS NULL
              AND EXISTS (
                  SELECT 1 FROM json_each(labels) je
                  WHERE je.value IN ('claimable', 'fleet:claimable')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM json_each(labels) je
                  JOIN project_tasks bt ON 'blocked-on:' || bt.id = je.value
                  AND bt.project_id = project_tasks.project_id
                  WHERE bt.status NOT IN ('closed', 'cancelled')
              )
            ORDER BY priority DESC, created_at ASC
        """

        tasks = []
        async with self._task_store._read(sql, params) as cur:
            rows = await cur.fetchall()
            desc = cur.description
            for row in rows:
                t = dict(zip([d[0] for d in desc], row))
                t["labels"] = json.loads(t.get("labels") or "[]")
                tasks.append(t)

        # Build per-agent claimed counts
        agent_claimed: dict[str, int] = {}
        for cid in eligible_set:
            agent_claimed[cid] = await self._get_agent_claimed_count(cid)

        results: list[tuple[dict, str]] = []
        for task in tasks:
            best_agent = None
            best_count = None
            for cid in eligible_set:
                count = agent_claimed.get(cid, 0)
                if count >= max_cc:
                    continue
                if best_agent is None or count < best_count:
                    best_agent = cid
                    best_count = count
            if best_agent is not None:
                results.append((task, best_agent))

        return results

    async def dispatch_cycle(self, user_id: str) -> None:
        cfg = await self.get_config(user_id)
        if not cfg.get("enabled"):
            return

        candidates = await self._get_candidates_for_user(user_id)
        if not candidates:
            return

        dispatched: set[str] = set()
        for task, agent_cid in candidates:
            if agent_cid in dispatched:
                continue
            try:
                await self._dispatch_one(task, agent_cid)
                dispatched.add(agent_cid)
            except Exception:
                logger.exception(
                    "dispatch failed for task %s -> agent %s", task.get("id"), agent_cid
                )

    async def _dispatch_one(self, task: dict, canonical_id: str) -> None:
        task_id = task["id"]
        project_id = task["project_id"]

        # Assign via existing task update path (audit-logged)
        await self._task_store.update_task(task_id, assignee_id=canonical_id)

        # Wake agent via existing heartbeat machinery
        agent_config = await self._resolve_agent_by_canonical_id(canonical_id)
        if agent_config is None:
            return

        from tinyagentos.agent_heartbeat import _wake_agent_with_task

        enqueued = await _wake_agent_with_task(self._app_state, agent_config, task)
        if enqueued:
            data_dir = getattr(self._app_state, "data_dir", None)
            if data_dir is not None:
                try:
                    from tinyagentos.wake_budget import record_scheduled_wake
                    record_scheduled_wake(data_dir, canonical_id, project_id)
                except Exception:
                    logger.warning(
                        "dispatcher: record_scheduled_wake failed for %s", task_id, exc_info=True
                    )


async def dispatcher_tick_loop(app_state, interval: float = 30) -> None:
    """Sweep enabled users' dispatcher configs every *interval* seconds and
    dispatch one card per eligible agent per user. No-op (cheap) while no user
    has dispatcher enabled."""
    from tinyagentos.projects.dispatcher import DispatcherService

    service: DispatcherService | None = None

    while True:
        try:
            if service is None:
                task_store = getattr(app_state, "project_task_store", None)
                grants_store = getattr(app_state, "agent_grants", None)
                registry_store = getattr(app_state, "agent_registry", None)
                project_store = getattr(app_state, "project_store", None)
                dispatcher_store = getattr(app_state, "dispatcher_store", None)
                if (
                    task_store is None
                    or grants_store is None
                    or registry_store is None
                    or project_store is None
                    or dispatcher_store is None
                ):
                    await asyncio_sleep(interval)
                    continue
                service = DispatcherService(
                    app_state=app_state,
                    dispatcher_store=dispatcher_store,
                    project_task_store=task_store,
                    agent_grants_store=grants_store,
                    agent_registry_store=registry_store,
                    project_store=project_store,
                )

            if not hasattr(app_state, "_dispatcher_users"):
                app_state._dispatcher_users = set()

            config = getattr(app_state, "config", None)
            if config is None:
                await asyncio_sleep(interval)
                continue

            current_users = set()
            for agent in getattr(config, "agents", None) or []:
                user_id = agent.get("user_id")
                if not user_id:
                    continue
                current_users.add(user_id)

            stale = app_state._dispatcher_users - current_users
            app_state._dispatcher_users = current_users

            for user_id in current_users:
                try:
                    cfg = await service.get_config(user_id)
                    if cfg.get("enabled"):
                        await service.dispatch_cycle(user_id)
                except Exception:
                    logger.exception("dispatcher tick: cycle failed for user %s", user_id)

            for user_id in stale:
                try:
                    app_state._dispatcher_users.discard(user_id)
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatcher tick: sweep iteration crashed")
        await asyncio_sleep(interval)


async def asyncio_sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)
