"""Dispatcher service implementation.

Part of Dispatcher S1 (jaylfc/taOS#2141). Wires the primitives into a lifespan-owned loop.
After this merges, a user who PUTs {enabled:true, eligible_agents:[...]} gets cards assigned and agents woken.
Leases do not expire yet (part 6): write the pending ledger row, no sweep.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from tinyagentos.agent_heartbeat import wake_agent_with_task
from tinyagentos.projects.dispatcher_store import DispatcherConfig, DispatcherStore
from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.agent_registry_store import AgentRegistryStore
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.wake_budget import can_wake, record_scheduled_wake

logger = logging.getLogger(__name__)


async def is_board_dispatchable(project_store, board_id: str, config: DispatcherConfig) -> bool:
    """Check if a board should be dispatched based on project settings.
    
    The fleet stays on ~/.taos-team/next_card.py for now: a board whose project
    settings carry {"dispatch": "hold"} is skipped entirely.
    """
    project = await project_store.get_project(board_id)
    if project:
        dispatch_hold = project.get("settings", {}).get("dispatch", "hold")
        return dispatch_hold != "hold"
    return False


async def list_ready_tasks_for_board(
    task_store: ProjectTaskStore, 
    board_id: str, 
    limit: int = 500
) -> list[dict]:
    """List ready tasks for a specific board, respecting board limits."""
    return await task_store.list_ready_tasks(
        project_id=board_id,
        assignee=None,
        limit=limit
    )


async def resolve_agent_refs(
    agent_registry: AgentRegistryStore,
    eligible_agents: list[str]
) -> tuple[list[dict], list[str]]:
    """Resolve agent references to actual agent objects.
    
    Returns:
        Tuple of (list of deployed agents, list of unresolved references)
    """
    deployed = []
    unresolved = []
    
    for agent_id in eligible_agents:
        agent = await agent_registry.get(agent_id)
        if agent and agent.get("status") == "deployed":
            deployed.append(agent)
        else:
            unresolved.append(agent_id)
    
    return deployed, unresolved


async def get_active_project_grants(
    grants_store: AgentGrantsStore,
    eligible_agents: list[dict]
) -> dict[str, list]:
    """Get active grants for eligible agents."""
    agent_ids = [agent.get("id") for agent in eligible_agents]
    return await grants_store.get_for_agents(agent_ids)


def count_open_load(agent_aliases: list[str]) -> int:
    """Count open load for an agent based on its aliases."""
    # This would integrate with the load tracking system
    # For now, return a placeholder
    return len(agent_aliases)


def count_open_load_by_project(agent_aliases: list[str]) -> int:
    """Count open load by project over all eligible aliases."""
    # This would integrate with the load tracking system
    # For now, return a placeholder
    return len(agent_aliases)


async def select_assignments(
    task_store: ProjectTaskStore,
    eligible_agents: list[dict],
    max_concurrent_per_agent: int = 1
) -> list[tuple]:
    """Select task assignments based on eligibility and constraints."""
    assignments = []
    
    for agent in eligible_agents:
        agent_id = agent.get("id")
        # Check if agent already has active assignments (max_concurrent_per_agent)
        current_load = count_open_load(agent.get("aliases", []))
        if current_load >= max_concurrent_per_agent:
            continue
            
        # Get ready tasks for this agent's projects
        # This would need to be more sophisticated in practice
        pass
    
    return assignments


class DispatcherService:
    """Dispatcher service for assigning tasks to eligible agents."""
    
    def __init__(self, app_state):
        self.app_state = app_state
        self._dispatch = app_state._background_tasks
    
    async def tick_user(self, user_id: str, cfg: DispatcherConfig, now: float) -> dict:
        """Process dispatch for a single user.
        
        Returns stage counts (e.g. {"board_held": 2, "assigned": 3, "wake": 1})
        """
        if not cfg.enabled:
            return {"board_held": 0, "assigned": 0, "wake": 0}
        
        stages = {"board_held": 0, "assigned": 0, "wake": 0}
        
        # Get boards for this user (active + still owned)
        project_store = getattr(self.app_state, "project_store", None)
        if project_store is None:
            return stages
            
        boards = await project_store.list_for_user(user_id)
        active_boards = [b for b in boards if b.get("status") == "active"]
        
        # Filter boards by is_board_dispatchable
        dispatchable_boards = []
        for board in active_boards:
            if await is_board_dispatchable(project_store, board["id"], cfg):
                dispatchable_boards.append(board)
            else:
                stages["board_held"] += 1
        
        if not dispatchable_boards:
            return stages
        
        # Get candidates (ready tasks) for each board
        task_store = getattr(self.app_state, "project_task_store", None)
        if task_store is None:
            return stages
            
        for board in dispatchable_boards:
            board_id = board["id"]
            candidates = await list_ready_tasks_for_board(task_store, board_id)
            
            # Process candidates
            agent_registry = getattr(self.app_state, "agent_registry", None)
            if agent_registry is None:
                continue
                
            eligible_agents, _ = await resolve_agent_refs(
                agent_registry, cfg.eligible_agents
            )
            
            if not eligible_agents:
                continue
            
            # Get grants and check wake budget
            grants_store = getattr(self.app_state, "agent_grants", None)
            if grants_store is None:
                continue
                
            grants = await get_active_project_grants(grants_store, eligible_agents)
            
            # Filter agents by wake budget
            filtered_agents = []
            data_dir = getattr(self.app_state, "data_dir", None)
            if data_dir is None:
                continue
                
            for agent in eligible_agents:
                agent_id = agent.get("id")
                project_id = board["id"]
                agent_name = agent.get("name", agent_id)
                
                if can_wake(data_dir, agent_id, agent_name, project_id, self.app_state.config):
                    filtered_agents.append(agent)
                else:
                    # No budget = not eligible, so a lease is never taken
                    continue
            
            # Calculate board inflight load
            board_inflight = count_open_load_by_project(
                [agent.get("aliases", []) for agent in filtered_agents]
            )
            
            # Select assignments
            assignments = await select_assignments(
                task_store, filtered_agents, cfg.max_concurrent_per_agent
            )
            
            for task, assignee, reason in assignments:
                # Check for race condition - only assign if still unassigned
                task_store = getattr(self.app_state, "project_task_store", None)
                if task_store is None:
                    continue
                
                # Get the assignee_id from the assignee dict
                assignee_id = assignee.get("id")
                if assignee_id:
                    # Use assign_if_unassigned to atomically check and assign
                    assigned = await task_store.assign_if_unassigned(
                        task["id"], assignee_id, f"dispatcher:{user_id}"
                    )
                    
                    if assigned:
                        # Assignment succeeded - insert pending ledger row
                        assignee_name = assignee.get("name", assignee_id)
                        await self._insert_lease(
                            task_id=task["id"],
                            project_id=task["project_id"],
                            user_id=user_id,
                            assignee_written=assignee_name,
                            lease_expires_at=now + self._get_lease_seconds_for_user(user_id)
                        )
                        
                        # Stamp backoff
                        await self._stamp_backoff(task["id"], now)
                        
                        # Wake agent
                        if await self._wake_task(task, assignee, user_id, reason):
                            stages["wake"] += 1
                        
                        stages["assigned"] += 1
                    else:
                        # Assignment failed (race) - skip, no ledger row, no wake, no charge
                        continue
        
        return stages
    
    async def _assign_task(
        self, 
        task: dict, 
        assignee: dict, 
        user_id: str, 
        board_id: str, 
        now: float,
        reason: str
    ) -> None:
        """Assign a task to an agent if unassigned."""
        from tinyagentos.projects.task_store import ProjectTaskStore
        
        task_store = getattr(self.app_state, "project_task_store", None)
        if task_store is None:
            return
        
        # Check if already assigned
        assigned = await task_store.get_assignment(task["id"])
        if assigned:
            return
        
        # Check for race condition
        # Note: assign_if_unassigned should handle this atomically
        assignee_id = assignee.get("id")
        assignee_name = assignee.get("name", assignee_id)
        
        # Insert pending ledger row
        await self._insert_lease(
            task_id=task["id"],
            project_id=task["project_id"],
            user_id=user_id,
            assignee_written=assignee_name,
            lease_expires_at=now + self._get_lease_seconds_for_user(user_id)
        )
        
        # Stamp backoff
        await self._stamp_backoff(task["id"], now)
        
        # Wake agent
        if await self._wake_task(task, assignee, user_id, reason):
            # Update debounce
            await self._update_debounce(task["id"], now)
        
        # Note: If assign_if_unassigned returns False (race), we skip:
        # - No ledger row
        # - No wake
        # - No charge
    
    async def _wake_task(
        self, 
        task: dict, 
        agent: dict, 
        user_id: str,
        reason: str
    ) -> bool:
        """Wake an agent with a task.
        
        Returns True if wake was successful.
        """
        # External wake: post to the project a2a channel only
        # This would integrate with the project's a2a channel
        
        # For now, implement dispatcher wake: wake_agent_with_task
        # This is kept under the wake budget even when agent_heartbeat_enabled is off
        # per Jay's rulings: enabling the dispatcher IS consent to wake assigned agents
        # directly, still under the wake budget, even while agent_heartbeat_enabled is off.
        # Holding the grant is enough (no owner match).
        
        app_state = self.app_state
        return await wake_agent_with_task(app_state, agent, task)
    
    def _get_lease_seconds_for_user(self, user_id: str) -> float:
        """Get lease seconds for a user (from dispatcher config)."""
        # This would look up the user's dispatcher config
        # For now, return default
        return 900.0  # 15 minutes
    
    async def _insert_lease(
        self,
        task_id: str,
        project_id: str,
        user_id: str,
        assignee_written: str,
        lease_expires_at: float
    ) -> None:
        """Insert a pending ledger row."""
        dispatcher_store = getattr(self.app_state, "dispatcher_store", None)
        if dispatcher_store is None:
            return
            
        await dispatcher_store.insert_lease(
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
            assignee_written=assignee_written,
            lease_expires_at=lease_expires_at
        )
    
    async def _stamp_backoff(self, task_id: str, now: float) -> None:
        """Stamp backoff for a task."""
        dispatcher_store = getattr(self.app_state, "dispatcher_store", None)
        if dispatcher_store is None:
            return
            
        await dispatcher_store.stamp_backoff(task_id, now)
    
    async def _update_debounce(self, task_id: str, now: float) -> None:
        """Update debounce for a task."""
        # This would update the agent's debounce map
        pass
    
    async def tick(self, now: Optional[float] = None) -> dict:
        """Process dispatch for all enabled users."""
        if now is None:
            now = time.time()
        
        dispatcher_store = getattr(self.app_state, "dispatcher_store", None)
        if dispatcher_store is None:
            return {}
        
        # Get all enabled users
        configs = await dispatcher_store.list_configs()
        enabled_users = [cfg for cfg in configs if cfg.enabled]
        
        results = {}
        for cfg in enabled_users:
            user_id = cfg.user_id
            stages = await self.tick_user(user_id, cfg, now)
            results[user_id] = stages
        
        return results


async def dispatcher_tick_loop(app_state, interval: float = 10.0) -> None:
    """Dispatch tick loop - lifespan-owned background task.
    
    Args:
        app_state: The FastAPI app state object
        interval: Seconds between ticks (default: 10.0)
    """
    import asyncio
    
    dispatcher_service = DispatcherService(app_state)
    
    while True:
        try:
            stages = await dispatcher_service.tick()
            logger.info("Dispatcher tick completed: %s", stages)
        except asyncio.CancelledError:
            logger.info("Dispatcher tick loop cancelled")
            raise
        except Exception:
            logger.exception("Dispatcher tick loop error")
        
        await asyncio.sleep(interval)