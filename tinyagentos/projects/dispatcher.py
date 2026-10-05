"""Dispatcher service - performs lease reconciliation and task assignment.

Part of Dispatcher S1 (jaylfc/taOS#2141). Implements the dispatcher loop
that reconciles leases, handles per-agent back-off, and enforces card expiry caps.
"""
from __future__ import annotations

import time
from typing import Any

from .dispatcher_store import DispatcherStore


class DispatcherService:
    """Dispatcher service that runs per-user ticks."""

    def __init__(self, store: DispatcherStore) -> None:
        self.store = store

    async def reconcile_user(self, user_id: str, now: float) -> None:
        """Reconcile leases for a user.

        Called BEFORE candidate gathering in tick_user and for every user with
        pending ledger rows even while that user's config is disabled.
        """
        # Fetch pending ledger rows for the user
        pending = await self.store.list_pending(user_id)
        if not pending:
            return

        # Get the user's config to get lease_seconds (guarded against None)
        config = await self.store.get_config(user_id)
        lease_seconds = config.lease_seconds if config else 900

        for row in pending:
            ledger_id = row["id"]
            task_id = row["task_id"]
            project_id = row["project_id"]
            canonical_id = row["canonical_id"]
            assignee_written = row["assignee_written"]
            state = row["state"]
            lease_expires_at = row["lease_expires_at"]

            # If the card is closed, parked, or quarantined, set state to closed and skip
            if state in ("closed", "parked", "quarantined"):
                await self.store.set_state(ledger_id, "closed", reason="card closed/parked/quarantined", resolved_at=now)
                continue

            # If the agent has claimed the task (assignee_written matches canonical_id)
            if assignee_written == canonical_id and assignee_written != "":
                # Set state to claimed and reset backoff for the agent
                await self.store.set_state(
                    ledger_id, "claimed", reason="", resolved_at=now
                )
                await self.store.reset_backoff(canonical_id)
                continue

            # If someone else has claimed the task (assignee_written is not empty and not equal to canonical_id)
            if assignee_written != "" and assignee_written != canonical_id:
                # Set state to overridden and do not touch the card again
                await self.store.set_state(
                    ledger_id, "overridden", reason="human reassigned", resolved_at=now
                )
                continue

            # Check if the lease has expired
            if now > lease_expires_at:
                # Task has expired and is unassigned -> set to expired and increment backoff
                await self.store.set_state(
                    ledger_id, "expired", reason="lease expired", resolved_at=now
                )
                await self.store.bump_backoff(canonical_id, now, lease_seconds)
                continue

            # If the lease has not expired and no claim, we do nothing for now.
            # The task will be considered for assignment in the tick_user function.

    async def tick_user(self, user_id: str, now: float) -> None:
        """Process one tick for a user.

        Steps:
          1. Reconcile leases (reconcile_user)
          2. Gather candidates (from store and policy)
          3. Exclude backed-off agents and paused cards
          4. Assign tasks (using policy with anti-affinity)
          5. Update ledger with new assignments
        """
        # First, reconcile leases
        await self.reconcile_user(user_id, now)

        # Get user config for eligible agents
        config = await self.store.get_config(user_id)
        if not config or not config.eligible_agents:
            return

        # Gather candidate ledger rows for the user (pending and not closed/overridden)
        candidates = await self.store.list_pending(user_id)
        if not candidates:
            return

        # Get backed-off agents (now < next_eligible_at)
        backed_off_agents = set(await self.store.list_backed_off_agents(now))
        # Get paused cards (>= CARD_EXPIRY_CAP expired rows in last 48h)
        paused_cards = set(await self.store.list_paused_cards(now))

        # Filter candidates: exclude those with card paused
        # Do NOT exclude by canonical_id being backed off - we handle that at assignment time
        filtered_candidates = []
        for row in candidates:
            if row["project_id"] in paused_cards:
                continue
            filtered_candidates.append(row)

        if not filtered_candidates:
            return

        # For each candidate, pick an eligible agent that is not backed off
        # Anti-affinity: prefer agents other than the canonical_id if it has backoff history
        assignments = []
        for row in filtered_candidates:
            canonical_id = row["canonical_id"]
            project_id = row["project_id"]
            
            # Find eligible agents for this user that are not backed off
            available_agents = [
                agent_id for agent_id in config.eligible_agents
                if agent_id not in backed_off_agents
            ]
            
            if not available_agents:
                continue
            
            # Anti-affinity: if canonical_id is in available_agents but has a history of expiries,
            # prefer another agent. Check if canonical_id has consecutive_expiries > 0.
            chosen_agent = canonical_id
            if canonical_id in available_agents:
                # Check if this agent has backoff history
                async with self.store._read(
                    "SELECT consecutive_expiries FROM dispatch_agent_backoff WHERE canonical_id = ?",
                    (canonical_id,),
                ) as cur:
                    backoff_row = await cur.fetchone()
                    if backoff_row and backoff_row[0] > 0:
                        # Agent has expiry history, try to pick another available agent
                        other_agents = [a for a in available_agents if a != canonical_id]
                        if other_agents:
                            chosen_agent = other_agents[0]
            else:
                # canonical_id not available (backed off or not eligible), pick first available
                chosen_agent = available_agents[0]
            
            assignments.append((row["id"], chosen_agent, project_id))

        # Update the ledger with new assignments
        for ledger_id, chosen_agent, project_id in assignments:
            # Set assignee_written to the chosen_agent
            async with self.store._tx():
                await self.store._db.execute(
                    """
                    UPDATE dispatch_ledger
                    SET assignee_written = ?
                    WHERE id = ?
                    """,
                    (chosen_agent, ledger_id),
                )
            # Set state to claimed and reset backoff
            await self.store.set_state(ledger_id, "claimed", reason="", resolved_at=now)
            await self.store.reset_backoff(chosen_agent)