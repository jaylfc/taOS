"""Dispatcher Service v2 — per-user dispatcher tick logic.

This module implements the dispatcher's per-user tick logic using real stores
and the pure dispatch policy. It is designed to be called from a background
loop (D3, tsk-mzf37f) but contains no loop logic itself — just the pure
tick_user/tick functions.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any

from tinyagentos.projects.dispatch_policy import (
    Assignment,
    is_board_dispatchable,
    is_candidate,
    select_assignments,
)
from tinyagentos.projects.agent_ref import AgentRef, resolve_agent_refs
from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.dispatcher_store import DispatcherStore, DispatcherConfig
from tinyagentos.agent_token_auth import active_project_grants
from tinyagentos.wake_budget import can_wake, record_scheduled_wake
from tinyagentos.agent_heartbeat import wake_agent_with_task
from tinyagentos.projects.a2a import ensure_a2a_channel

logger = logging.getLogger(__name__)


class DispatcherService:
    """Per-user dispatcher service operating on real stores."""

    def __init__(self, app_state: Any) -> None:
        self.app_state = app_state

    async def tick_user(self, user_id: str, cfg: DispatcherConfig, now: float) -> dict:
        """Run one dispatcher tick for a single user.

        Returns a dict with stage counts:
        - boards_held: number of boards considered (active, owned, dispatchable)
        - candidates_considered: total claimable cards across boards
        - assignments_made: number of successful assignments (leases inserted)
        - races_skipped: number of assign_if_unassigned races lost
        - wakes: number of successful wake_agent_with_task / a2a announces
        """
        data_dir = getattr(self.app_state, "data_dir", None)
        if data_dir is None:
            raise RuntimeError("data_dir is required for wake-budget enforcement")

        project_store: ProjectStore = self.app_state.project_store
        task_store: ProjectTaskStore = self.app_state.project_task_store
        dispatcher_store: DispatcherStore = self.app_state.dispatcher_store
        agent_registry = self.app_state.agent_registry
        agent_grants = self.app_state.agent_grants
        config = getattr(self.app_state, "config", None)
        chat_channels = getattr(self.app_state, "chat_channels", None)
        chat_messages = getattr(self.app_state, "chat_messages", None)

        # Determine boards to process
        if cfg.boards:
            board_ids = cfg.boards
        else:
            owned_projects = await project_store.list_for_user(user_id, status="active")
            board_ids = [p["id"] for p in owned_projects]

        boards_held = 0
        candidates_considered = 0
        assignments_made = 0
        races_skipped = 0
        wakes = 0

        # Resolve eligible agents once per user
        eligible_canonical_ids = cfg.eligible_agents or []
        agent_refs = await resolve_agent_refs(
            eligible_canonical_ids, config, agent_registry
        )

        for board_id in board_ids:
            # Re-check board is active and owned by this user every tick
            project = await project_store.get_project(board_id)
            if project is None:
                continue
            if project.get("user_id") != user_id:
                continue
            if project.get("status") != "active":
                continue

            # Check board-level dispatch hold
            if not is_board_dispatchable(project):
                continue

            boards_held += 1

            # Get ready tasks for this board
            candidates = await task_store.list_ready_tasks(board_id, limit=500)
            # Filter by is_candidate
            filtered_candidates = []
            for task in candidates:
                ok, _reason = is_candidate(task)
                if ok:
                    filtered_candidates.append(task)

            candidates_considered += len(filtered_candidates)
            if not filtered_candidates:
                continue

            # Build eligible agents for this board (must have grant + wake budget)
            board_agent_refs = []
            for ref in agent_refs:
                grants = await active_project_grants(
                    agent_registry, agent_grants, ref.canonical_id, "project_tasks"
                )
                if board_id not in grants:
                    continue
                # Check wake budget
                agent_id_for_budget = ref.config_id if ref.deployed else ref.canonical_id
                agent_name = ref.name or ref.canonical_id
                if not can_wake(data_dir, agent_id_for_budget, agent_name, board_id, config):
                    continue
                board_agent_refs.append(ref)

            if not board_agent_refs:
                continue

            # Build grants dict for select_assignments
            grants_dict = {}
            for ref in board_agent_refs:
                grants = await active_project_grants(
                    agent_registry, agent_grants, ref.canonical_id, "project_tasks"
                )
                grants_dict[ref.canonical_id] = list(grants)

            # Build load dict per agent
            load_dict = {}
            for ref in board_agent_refs:
                aliases = list(ref.aliases())
                agent_load = await task_store.count_open_load(aliases)
                load_dict[ref.canonical_id] = agent_load

            # Build board_inflight dict
            all_aliases = []
            for ref in board_agent_refs:
                all_aliases.extend(ref.aliases())
            board_inflight = await task_store.count_open_load_by_project(all_aliases)

            # Get recent expiries and card expiry counts
            recent_expiries = await dispatcher_store.recent_expiries(now - 48 * 3600)
            card_expiry_counts = await dispatcher_store.expired_counts_48h(now)

            # Get last_assigned_at map
            canonical_ids = [ref.canonical_id for ref in board_agent_refs]
            last_assigned_map = await dispatcher_store.last_assigned_map(canonical_ids)

            # Select assignments
            assignments = select_assignments(
                candidates=filtered_candidates,
                agents=[{"id": ref.canonical_id} for ref in board_agent_refs],
                grants=grants_dict,
                load=load_dict,
                board_inflight=board_inflight,
                last_assigned_at=last_assigned_map,
                recent_expiries=recent_expiries,
                card_expiry_counts=card_expiry_counts,
                cap=cfg.max_concurrent_per_agent,
            )

            # Process each assignment
            for assignment in assignments:
                # Find the AgentRef for this canonical_id
                ref = next((r for r in board_agent_refs if r.canonical_id == assignment.canonical_id), None)
                if ref is None:
                    continue

                assignee_value = ref.assignee_value()
                # Try to assign
                assigned = await task_store.assign_if_unassigned(
                    assignment.task_id, assignee_value, f"dispatcher:{user_id}"
                )
                if not assigned:
                    races_skipped += 1
                    continue

                # Insert lease
                lease_expires_at = now + cfg.lease_seconds
                lease_id = await dispatcher_store.insert_lease(
                    user_id=user_id,
                    task_id=assignment.task_id,
                    project_id=assignment.project_id,
                    canonical_id=assignment.canonical_id,
                    assignee_written=assignee_value,
                    reason=assignment.reason,
                    assigned_at=now,
                    lease_expires_at=lease_expires_at,
                )
                if lease_id is None:
                    # Should not happen since we just assigned, but handle gracefully
                    races_skipped += 1
                    continue

                # Stamp last assigned
                await dispatcher_store.stamp_last_assigned(assignment.canonical_id, now)
                assignments_made += 1

                # Wake the agent
                task_row = await task_store.get_task(assignment.task_id)
                if task_row is None:
                    continue

                if ref.deployed and ref.config_id:
                    # DEPLOYED agent: use wake_agent_with_task
                    # Find the agent dict in config.agents
                    agent_dict = None
                    for agent in getattr(config, "agents", None) or []:
                        if agent.get("id") == ref.config_id:
                            agent_dict = agent
                            break
                    if agent_dict is None:
                        logger.warning(
                            "dispatcher: deployed agent config_id %s not found in config.agents",
                            ref.config_id,
                        )
                        continue

                    woke = await wake_agent_with_task(self.app_state, agent_dict, task_row)
                    if woke:
                        record_scheduled_wake(data_dir, ref.config_id, task_row["project_id"])
                        wakes += 1
                else:
                    # EXTERNAL agent: mirror agent_heartbeat.py:124-144
                    if chat_channels is not None and chat_messages is not None and project_store is not None:
                        try:
                            channel = await ensure_a2a_channel(
                                chat_channels, project_store, task_row["project_id"], config=config
                            )
                            text = f"Dispatcher: you have a ready task {task_row['id']}: {task_row['title']}"
                            await chat_messages.send_message(
                                channel_id=channel["id"],
                                author_id=f"dispatcher:{user_id}",
                                author_type="system",
                                content=text,
                                content_type="system",
                                state="complete",
                            )
                            record_scheduled_wake(data_dir, ref.canonical_id, task_row["project_id"])
                            wakes += 1
                        except Exception:
                            logger.warning(
                                "dispatcher: a2a announce failed for task %s", task_row["id"], exc_info=True
                            )
                            # lease stands, no wake recorded

        return {
            "boards_held": boards_held,
            "candidates_considered": candidates_considered,
            "assignments_made": assignments_made,
            "races_skipped": races_skipped,
            "wakes": wakes,
        }

    async def tick(self, now: float | None = None) -> dict:
        """Run dispatcher tick for all enabled users.

        Returns aggregated counts across all users.
        """
        if now is None:
            now = time.time()

        dispatcher_store: DispatcherStore = self.app_state.dispatcher_store
        configs = await dispatcher_store.list_configs()

        total = {
            "boards_held": 0,
            "candidates_considered": 0,
            "assignments_made": 0,
            "races_skipped": 0,
            "wakes": 0,
            "users_processed": 0,
            "users_failed": 0,
        }

        for cfg in configs:
            if not cfg.enabled:
                continue
            try:
                result = await self.tick_user(cfg.user_id, cfg, now)
                for k in ("boards_held", "candidates_considered", "assignments_made", "races_skipped", "wakes"):
                    total[k] += result[k]
                total["users_processed"] += 1
            except Exception:
                logger.exception("dispatcher: tick failed for user %s", cfg.user_id)
                total["users_failed"] += 1

        return total