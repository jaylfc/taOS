from __future__ import annotations

import asyncio
import json
import logging
import time

from tinyagentos.projects.tx import ProjectsDBStore
from tinyagentos.projects.task_store import _row_to_task
from tinyagentos.wake_budget import can_wake, record_scheduled_wake

logger = logging.getLogger(__name__)

DISPATCHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatcher_config (
    user_id TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (user_id)
);
"""

_DEFAULT_CONFIG = {
    "enabled": False,
    "boards": [],
    "eligible_agents": [],
    "max_concurrent_per_agent": 1,
    "poll_seconds": 30,
}


class DispatcherStore(ProjectsDBStore):
    SCHEMA = DISPATCHER_SCHEMA

    async def get_config(self, user_id: str) -> dict:
        async with self._read(
            "SELECT config FROM dispatcher_config WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return dict(_DEFAULT_CONFIG)
            try:
                cfg = json.loads(row[0])
            except json.JSONDecodeError:
                cfg = {}
            return {**_DEFAULT_CONFIG, **cfg}

    async def set_config(self, user_id: str, config: dict) -> None:
        current = await self.get_config(user_id)
        current.update(config)
        async with self._tx():
            await self._db.execute(
                """INSERT INTO dispatcher_config (user_id, config)
                   VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET config = excluded.config""",
                (user_id, json.dumps(current)),
            )

    async def get_all_configs(self) -> list[dict]:
        async with self._read("SELECT user_id, config FROM dispatcher_config") as cur:
            rows = await cur.fetchall()
            result = []
            for row in rows:
                try:
                    cfg = json.loads(row[1])
                except json.JSONDecodeError:
                    cfg = {}
                result.append({"user_id": row[0], "config": {**_DEFAULT_CONFIG, **cfg}})
            return result


class DispatcherService:
    def __init__(self, app_state) -> None:
        self._app_state = app_state

    async def dispatch_once(self) -> None:
        store = getattr(self._app_state, "dispatcher_store", None)
        if store is None:
            return

        project_task_store = getattr(self._app_state, "project_task_store", None)
        agent_grants_store = getattr(self._app_state, "agent_grants", None)
        project_store = getattr(self._app_state, "project_store", None)

        if not all([project_task_store, agent_grants_store, project_store]):
            return

        try:
            configs = await store.get_all_configs()
        except Exception:
            return

        for entry in configs:
            user_id = entry["user_id"]
            cfg = entry.get("config", {})
            if not cfg.get("enabled", False):
                continue

            try:
                await self._dispatch_for_user(user_id, cfg)
            except Exception:
                logger.exception("dispatcher: dispatch failed for user %s", user_id)

    async def _dispatch_for_user(self, user_id: str, cfg: dict) -> None:
        project_task_store = self._app_state.project_task_store
        agent_grants_store = self._app_state.agent_grants
        project_store = self._app_state.project_store
        config = getattr(self._app_state, "config", None)
        data_dir = getattr(self._app_state, "data_dir", None)

        boards = cfg.get("boards") or []
        eligible_agents = cfg.get("eligible_agents") or []
        max_concurrent = cfg.get("max_concurrent_per_agent", 1)

        if not boards:
            projects = await project_store.list_for_user(user_id)
            boards = [p["id"] for p in projects]

        if not boards or not eligible_agents:
            return

        candidates: list[dict] = []
        for board_id in boards:
            board_tasks = await self._get_candidates(project_task_store, board_id)
            candidates.extend(board_tasks)

        candidates.sort(
            key=lambda t: (-(t.get("priority") or 0), t.get("created_at") or 0)
        )

        assigned_tasks: set[str] = set()

        for agent_id in eligible_agents:
            claimed_count = await self._count_claimed_tasks(
                project_task_store, agent_id
            )
            if claimed_count >= max_concurrent:
                continue

            for task in candidates:
                if task["id"] in assigned_tasks:
                    continue

                if not await self._agent_has_grant(
                    agent_grants_store, agent_id, task["project_id"]
                ):
                    continue

                if await self._is_blocked(project_task_store, task):
                    continue

                await self._dispatch_task(task, agent_id, data_dir, config)
                assigned_tasks.add(task["id"])
                break

    async def _get_candidates(self, project_task_store, board_id: str) -> list[dict]:
        statuses = ("open", "todo", "ready", "backlog")
        placeholders = ",".join("?" * len(statuses))

        async with project_task_store._read(
            f"""SELECT * FROM project_tasks
               WHERE project_id = ?
                 AND status IN ({placeholders})
                 AND claimed_by IS NULL
                 AND (
                     assignee_id IS NULL
                     OR assignee_id = ''
                     OR assignee_id = '@any'
                 )
               ORDER BY priority DESC, created_at ASC""",
            [board_id, *statuses],
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
            tasks = [_row_to_task(r, desc) for r in rows]

        return [
            t
            for t in tasks
            if any(l in ("claimable", "fleet:claimable") for l in (t.get("labels") or []))
        ]

    async def _is_blocked(self, project_task_store, task: dict) -> bool:
        task_id = task["id"]
        project_id = task["project_id"]

        labels = task.get("labels") or []
        for label in labels:
            if label.startswith("blocked-on:"):
                dep_id = label[len("blocked-on:") :]
                dep = await project_task_store.get_task(dep_id)
                if dep is None:
                    continue
                if dep["project_id"] != project_id:
                    continue
                if dep["status"] not in ("closed", "cancelled"):
                    return True

        rels = await project_task_store.list_relationships(task_id, direction="from")
        for rel in rels:
            if rel["kind"] == "blocks":
                blocker = await project_task_store.get_task(rel["to_task_id"])
                if blocker is None:
                    continue
                if blocker["project_id"] != project_id:
                    continue
                if blocker["status"] not in ("closed", "cancelled"):
                    return True

        return False

    async def _agent_has_grant(
        self, agent_grants_store, agent_id: str, project_id: str
    ) -> bool:
        grants = await agent_grants_store.list_grants(agent_id)
        for g in grants:
            if g["scope"] == "project_tasks" and g["project_id"] == project_id:
                return True
        return False

    async def _count_claimed_tasks(self, project_task_store, agent_id: str) -> int:
        async with project_task_store._read(
            "SELECT COUNT(*) FROM project_tasks WHERE claimed_by = ? AND status = 'claimed'",
            (agent_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def _dispatch_task(self, task: dict, agent_id: str, data_dir, config) -> None:
        project_task_store = self._app_state.project_task_store
        config_obj = getattr(self._app_state, "config", None)

        agent = None
        for a in getattr(config_obj, "agents", None) or []:
            if a.get("id") == agent_id or a.get("canonical_id") == agent_id:
                agent = a
                break

        await project_task_store.update_task(task["id"], assignee_id=agent_id)

        if agent is None:
            return

        if not can_wake(
            data_dir, agent_id, agent.get("name", agent_id), task["project_id"], config_obj
        ):
            return

        from tinyagentos.agent_heartbeat import _wake_agent_with_task

        if await _wake_agent_with_task(self._app_state, agent, task):
            record_scheduled_wake(data_dir, agent_id, task["project_id"])


async def dispatcher_loop(app_state, interval: float = 30.0) -> None:
    service = DispatcherService(app_state)
    while True:
        try:
            await service.dispatch_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("dispatcher: sweep iteration crashed")
        await asyncio.sleep(interval)
