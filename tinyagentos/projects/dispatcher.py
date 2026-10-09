"""Dispatcher service — wires tick_user/tick to the MERGED dispatch primitives."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List

from tinyagentos.agent_token_auth import active_project_grants
from tinyagentos.projects.agent_ref import resolve_agent_refs
from tinyagentos.projects.dispatch_policy import (
    Assignment,
    is_board_dispatchable,
    is_candidate,
    select_assignments,
)
from tinyagentos.projects.dispatcher_store import DispatcherStore
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.wake_budget import can_wake, record_scheduled_wake

logger = logging.getLogger(__name__)

# Import wake_agent_with_task from agent_heartbeat for patch testing
from tinyagentos.agent_heartbeat import wake_agent_with_task


class DispatcherService:
    """Dispatcher service — wires tick_user/tick to the MERGED dispatch primitives."""

    def __init__(self, app_state: Any):
        self.app_state = app_state

        # Store references to commonly used stores for efficiency
        self.project_store = getattr(app_state, "project_store", None)
        self.project_task_store = getattr(app_state, "project_task_store", None)
        self.dispatcher_store = getattr(app_state, "dispatcher_store", None)
        self.agent_registry = getattr(app_state, "agent_registry", None)

        # Validate required components
        if self.project_store is None:
            raise ValueError("app_state must have project_store")
        if self.project_task_store is None:
            raise ValueError("app_state must have project_task_store")
        if self.dispatcher_store is None:
            raise ValueError("app_state must have dispatcher_store")
        if self.agent_registry is None:
            raise ValueError("app_state must have agent_registry")

    async def tick_user(self, user_id: str, cfg: Dict[str, Any], now: float) -> Dict[str, int]:
        """Dispatch for a single user.

        Returns stage counts (boards_held, candidates_considered, assignments_made).
        """
        data_dir = getattr(self.app_state, "data_dir", None)
        if data_dir is None:
            raise RuntimeError("data_dir is required for wake-budget enforcement")

        # Determine boards: cfg.boards or all active projects owned by user_id
        boards = cfg.get("boards", [])
        if not boards:
            # Re-checked active + owned every tick
            owned_projects = await self.project_store.list_for_user(user_id, status="active")
            boards = [p["id"] for p in owned_projects]
        else:
            # If boards are specified in config, we still need to get the projects for checking
            # We get all owned projects and filter by the specified boards to re-check ownership/activity
            owned_projects = await self.project_store.list_for_user(user_id, status="active")
            # Filter the boards to only include those that are owned and active
            filtered_boards = []
            for board_id in boards:
                project = next((p for p in owned_projects if p["id"] == board_id), None)
                if project:
                    filtered_boards.append(board_id)
            boards = filtered_boards

        # Stage counts
        stage_counts = {"boards_held": 0, "candidates_considered": 0, "assignments_made": 0}

        # Get projects for board check
        projects_by_id = {}
        for project in owned_projects:
            projects_by_id[project["id"]] = project

        # Process each board
        for board_id in boards:
            # Check if board is dispatchable (not held)
            project = projects_by_id.get(board_id)
            if not project or not is_board_dispatchable(project):
                stage_counts["boards_held"] += 1
                continue

            # Get ready tasks filtered by is_candidate
            candidates = []
            ready_tasks = await self.project_task_store.list_ready_tasks(board_id, limit=500)
            for task in ready_tasks:
                ok, reason = is_candidate(task)
                if ok:
                    stage_counts["candidates_considered"] += 1
                    candidates.append(task)

            if not candidates:
                continue

            # Resolve agent references
            eligible_agents = await resolve_agent_refs(
                cfg.get("eligible_agents", []), self.app_state.config, self.agent_registry
            )

            # Filter agents based on can_wake (no budget = not eligible)
            eligible_refs = []
            for ref in eligible_agents:
                agent_id = ref.assignee_value()
                agent_name = ref.name or ref.canonical_id
                config = self.app_state.config
                if can_wake(data_dir, agent_id, agent_name, board_id, config):
                    eligible_refs.append(ref)

            if not eligible_refs:
                continue

            # Get grants per agent
            grants = {}
            for ref in eligible_refs:
                agent_id = ref.assignee_value()
                projects = await active_project_grants(
                    self.agent_registry, self.app_state.agent_grants, agent_id, "wake", now
                )
                grants[agent_id] = list(projects)

            # Get load information
            # Get all agent aliases for load calculation
            all_aliases = []
            for ref in eligible_refs:
                all_aliases.extend(ref.aliases())

            load = await self.project_task_store.count_open_load(all_aliases)
            board_inflight = await self.project_task_store.count_open_load_by_project(all_aliases)

            # Get recent expiries from dispatcher store
            recent_expiries = await self.dispatcher_store.recent_expiries(now - 48 * 3600)

            # Get last assigned maps
            canonical_ids = [ref.assignee_value() for ref in eligible_refs]
            last_assigned_at = await self.dispatcher_store.last_assigned_map(canonical_ids)

            # Get card expiry counts from dispatcher store
            card_expiry_counts = await self.dispatcher_store.expired_counts_48h(now)

            # Select assignments
            assignments = select_assignments(
                candidates,
                eligible_refs,
                grants,
                load,
                board_inflight,
                last_assigned_at,
                recent_expiries,
                card_expiry_counts,
                cfg.get("max_concurrent_per_agent", 1),
            )

            # Process each assignment
            for assignment in assignments:
                # Assign the task
                assigned = await self.project_task_store.assign_if_unassigned(
                    assignment.task_id, assignment.canonical_id, f"dispatcher:{user_id}"
                )

                if assigned:
                    # Insert lease
                    lease_expires_at = now + cfg.get("lease_seconds", 900)
                    await self.dispatcher_store.insert_lease(
                        user_id=user_id,
                        task_id=assignment.task_id,
                        project_id=assignment.project_id,
                        canonical_id=assignment.canonical_id,
                        assignee_written=assignment.canonical_id,
                        reason=assignment.reason,
                        assigned_at=now,
                        lease_expires_at=lease_expires_at,
                    )

                    # Stamp last assigned
                    await self.dispatcher_store.stamp_last_assigned(assignment.canonical_id, now)

                    # Wake the agent
                    await self._wake_agent(assignment, data_dir, now, cfg, user_id)
                    stage_counts["assignments_made"] += 1

        return stage_counts

    async def _wake_agent(self, assignment: Assignment, data_dir: Any, now: float, cfg: Dict[str, Any], user_id: str) -> None:
        """Wake an agent for a task."""
        # For deployed agents: wake_agent_with_task + record_scheduled_wake (outside try/except)
        # For external agents: post to project a2a channel + record_scheduled_wake (inside try/except with warning)

        # Find the agent ref for this assignment
        # We need to get the actual agent configuration to determine if it's deployed
        agent_config = None
        for agent in self.app_state.config.agents:
            if agent.get("id") == assignment.canonical_id:
                agent_config = agent
                break

        if agent_config and agent_config.get("deployed"):
            # DEPLOYED agent
            woke = await wake_agent_with_task(self.app_state, agent_config, {
                "id": assignment.task_id,
                "title": assignment.task_id,  # We don't have title in Assignment
                "project_id": assignment.project_id,
            })
            # record_scheduled_wake OUTSIDE any try/except - must propagate on failure
            record_scheduled_wake(data_dir, assignment.canonical_id, assignment.project_id)
        else:
            # EXTERNAL agent - post to project a2a channel
            try:
                from tinyagentos.projects.a2a import ensure_a2a_channel

                chat_channels = getattr(self.app_state, "chat_channels", None)
                chat_messages = getattr(self.app_state, "chat_messages", None)
                project_store = getattr(self.app_state, "project_store", None)

                if chat_channels is not None and chat_messages is not None and project_store is not None:
                    channel = await ensure_a2a_channel(
                        chat_channels, project_store, assignment.project_id, config=self.app_state.config
                    )
                    await chat_messages.send_message(
                        channel_id=channel["id"],
                        author_id=f"dispatcher:{user_id}",
                        author_type="system",
                        content=f"Dispatcher assigned task {assignment.task_id} to agent {assignment.canonical_id}",
                        content_type="system",
                        state="complete",
                    )
                # record_scheduled_wake for external agents - wrap in try/except so lease stands
                record_scheduled_wake(data_dir, assignment.canonical_id, assignment.project_id)
            except Exception as e:
                logger.warning(f"Failed to wake external agent: {e}")
                # Lease was still inserted, so we still record the wake attempt
                record_scheduled_wake(data_dir, assignment.canonical_id, assignment.project_id)

    async def tick(self, now: float = None) -> None:
        """Dispatch for all enabled configs (every enabled config from DispatcherStore.list_configs()).

        Per-user try/except + logger.exception so one user cannot stop the others.
        """
        if now is None:
            now = time.time()

        # Get all dispatcher configs
        configs = await self.dispatcher_store.list_configs()

        for cfg in configs:
            if not cfg.get("enabled", False):
                continue

            try:
                # Process this user's config
                await self.tick_user(cfg.user_id, cfg.to_dict(), now)
            except Exception as e:
                logger.exception(f"Dispatcher tick failed for user {cfg.user_id}", exc_info=e)
                raise
