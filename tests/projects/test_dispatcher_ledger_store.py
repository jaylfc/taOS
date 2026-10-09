"""Tests for dispatcher ledger and backoff store (tsk-cbaoj7 / S1 part 5 slice).

These tests MUST FAIL on dev before the implementation is added.
"""
from __future__ import annotations

import time
import uuid
import pytest

from tinyagentos.projects.dispatcher_store import DispatcherStore


@pytest.fixture
def store(tmp_path):
    """Return a fresh DispatcherStore bound to a temporary projects.db."""
    db_path = tmp_path / "projects.db"
    store_ = DispatcherStore(db_path)
    # Initialize the store (creates tables)
    import asyncio

    async def init():
        await store_.init()

    asyncio.run(init())
    return store_


@pytest.mark.asyncio
async def test_insert_lease_returns_id_and_second_pending_for_same_task_returns_none(store):
    """First insert_lease returns a lease id; second for same task returns None due to pending unique index."""
    user_id = "user1"
    task_id = "task1"
    project_id = "proj1"
    canonical_id = "agent1"
    assignee_written = ""
    reason = "test"
    assigned_at = time.time()
    lease_expires_at = assigned_at + 300

    # First insert should succeed and return a lease id
    lease_id1 = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=canonical_id,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert lease_id1 is not None
    assert isinstance(lease_id1, str)
    assert len(lease_id1) == 32  # uuid4 hex

    # Second insert for the same task should return None (pending unique index violation)
    lease_id2 = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=canonical_id,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert lease_id2 is None


@pytest.mark.asyncio
async def test_pending_for_agent_lists_only_pending_rows_of_that_agent(store):
    """pending_for_agent returns only state='pending' rows for the given canonical_id."""
    user_id = "user1"
    task_id = "task1"
    project_id = "proj1"
    canonical_id = "agent1"
    assignee_written = ""
    reason = "test"
    assigned_at = time.time()
    lease_expires_at = assigned_at + 300

    # Insert a pending lease
    lease_id = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=canonical_id,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert lease_id is not None

    # Update that lease to expired state
    async with store._tx():
        await store._db.execute(
            "UPDATE dispatch_ledger SET state = 'expired', resolved_at = ? WHERE id = ?",
            (assigned_at + 600, lease_id),
        )

    # Now insert a new pending lease for the same task but different agent (should succeed because previous is expired)
    expired_canonical = "agent2"
    new_lease_id = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=expired_canonical,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert new_lease_id is not None

    # Call pending_for_agent for the first agent (should return empty because its lease is expired)
    pending = await store.pending_for_agent(canonical_id)
    assert len(pending) == 0

    # Call pending_for_agent for the second agent (should return the new pending lease)
    pending2 = await store.pending_for_agent(expired_canonical)
    assert len(pending2) == 1
    assert pending2[0]["id"] == new_lease_id
    assert pending2[0]["state"] == "pending"


@pytest.mark.asyncio
async def test_recent_expiries_groups_agents_by_task_since_cutoff(store):
    """recent_expiries returns task_id -> tuple of canonical_ids for state='expired' rows with resolved_at >= since."""
    user_id = "user1"
    task_id = "task1"
    project_id = "proj1"
    canonical_id1 = "agent1"
    canonical_id2 = "agent2"
    assignee_written = ""
    reason = "test"
    assigned_at = time.time()
    lease_expires_at = assigned_at + 300

    # Insert first lease and update to expired
    lease_id1 = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=canonical_id1,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert lease_id1 is not None
    resolved_at1 = assigned_at + 600  # 10 minutes later
    async with store._tx():
        await store._db.execute(
            "UPDATE dispatch_ledger SET state = 'expired', resolved_at = ? WHERE id = ?",
            (resolved_at1, lease_id1),
        )

    # Insert second lease and update to expired
    lease_id2 = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=canonical_id2,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert lease_id2 is not None
    resolved_at2 = assigned_at + 900  # 15 minutes later
    async with store._tx():
        await store._db.execute(
            "UPDATE dispatch_ledger SET state = 'expired', resolved_at = ? WHERE id = ?",
            (resolved_at2, lease_id2),
        )

    # Since cutoff is set to resolved_at1 + 1 (so only lease_id2 qualifies)
    since = resolved_at1 + 1
    result = await store.recent_expiries(since)
    # Expect task_id -> (canonical_id2,) because only lease_id2 has resolved_at >= since
    assert task_id in result
    assert result[task_id] == (canonical_id2,)

    # Now set since to assigned_at (so both resolved_at are >= since)
    since = assigned_at
    result = await store.recent_expiries(since)
    assert task_id in result
    # The tuple should contain both canonical_ids, but order is not guaranteed
    assert set(result[task_id]) == {canonical_id1, canonical_id2}
    assert len(result[task_id]) == 2


@pytest.mark.asyncio
async def test_expired_counts_48h_ignores_older_rows(store):
    """expired_counts_48h returns task_id -> count of state='expired' rows with resolved_at >= now - 48*3600."""
    user_id = "user1"
    task_id = "task1"
    project_id = "proj1"
    canonical_id = "agent1"
    assignee_written = ""
    reason = "test"
    assigned_at = time.time()
    lease_expires_at = assigned_at + 300

    # Insert a lease and update to expired with resolved_at in the past (older than 48 hours)
    lease_id = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=canonical_id,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert lease_id is not None

    old_resolved_at = assigned_at - (49 * 3600)  # 49 hours ago
    async with store._tx():
        await store._db.execute(
            "UPDATE dispatch_ledger SET state = 'expired', resolved_at = ? WHERE id = ?",
            (old_resolved_at, lease_id),
        )

    # Now call expired_counts_48h with now = assigned_at (so cutoff = assigned_at - 48*3600)
    now = assigned_at
    result = await store.expired_counts_48h(now)
    # The old row should be ignored because resolved_at < cutoff
    assert task_id not in result or result.get(task_id, 0) == 0

    # Now insert another lease and set resolved_at to recent (within 48 hours)
    lease_id2 = await store.insert_lease(
        user_id=user_id,
        task_id=task_id,
        project_id=project_id,
        canonical_id=canonical_id,
        assignee_written=assignee_written,
        reason=reason,
        assigned_at=assigned_at,
        lease_expires_at=lease_expires_at,
    )
    assert lease_id2 is not None
    recent_resolved_at = assigned_at - (12 * 3600)  # 12 hours ago
    async with store._tx():
        await store._db.execute(
            "UPDATE dispatch_ledger SET state = 'expired', resolved_at = ? WHERE id = ?",
            (recent_resolved_at, lease_id2),
        )

    result = await store.expired_counts_48h(now)
    assert result.get(task_id, 0) == 1  # only the recent row counts


@pytest.mark.asyncio
async def test_stamp_last_assigned_upserts_and_last_assigned_map_reads_it(store):
    """stamp_last_assigned upserts the last_assigned_at; last_assigned_map reads it back."""
    canonical_id = "agent1"
    ts1 = time.time()
    ts2 = ts1 + 100

    # First stamp should insert
    await store.stamp_last_assigned(canonical_id, ts1)

    # Map should return the timestamp
    mapping = await store.last_assigned_map([canonical_id])
    assert mapping[canonical_id] == ts1

    # Second stamp should update
    await store.stamp_last_assigned(canonical_id, ts2)
    mapping = await store.last_assigned_map([canonical_id])
    assert mapping[canonical_id] == ts2

    # Map with unknown agent should return absent key
    mapping2 = await store.last_assigned_map(["unknown-agent"])
    assert "unknown-agent" not in mapping2


@pytest.mark.asyncio
async def test_wake_agent_with_task_public_name_and_private_alias_are_the_same_function(store):
    """Ensure wake_agent_with_task and _wake_agent_with_task are the same function (alias)."""
    # This test does not require the store, but we need to import the function from agent_heartbeat
    from tinyagentos.agent_heartbeat import wake_agent_with_task, _wake_agent_with_task

    assert wake_agent_with_task is _wake_agent_with_task
