"""Tests for the dispatcher ledger and backoff store.

These tests MUST FAIL on dev before the implementation is added.
"""

import time
import pytest
import pytest_asyncio
from tinyagentos.projects.dispatcher_store import DispatcherStore


@pytest_asyncio.fixture
async def store(tmp_path):
    """Create and initialize a DispatcherStore for testing."""
    db_path = tmp_path / "projects.db"
    store = DispatcherStore(db_path)
    await store.init()
    try:
        yield store
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_insert_lease_returns_id_and_second_pending_for_same_task_returns_none(store):
    """Insert a lease returns an id, and a second pending for the same task returns None."""
    # Insert first lease
    lease_id1 = await store.insert_lease(
        user_id="user1",
        task_id="task1",
        project_id="project1",
        canonical_id="agent1",
        assignee_written="agent1",
        reason="test reason",
        assigned_at=1000.0,
        lease_expires_at=2000.0,
    )
    assert lease_id1 is not None
    assert isinstance(lease_id1, str)
    # Insert second lease for the same task (should fail due to unique pending index)
    lease_id2 = await store.insert_lease(
        user_id="user1",
        task_id="task1",
        project_id="project1",
        canonical_id="agent2",
        assignee_written="agent2",
        reason="test reason 2",
        assigned_at=1000.0,
        lease_expires_at=2000.0,
    )
    assert lease_id2 is None


@pytest.mark.asyncio
async def test_pending_for_agent_lists_only_pending_rows_of_that_agent(store):
    """pending_for_agent returns only pending rows for the given canonical_id."""
    # Insert a pending lease for agent1
    await store.insert_lease(
        user_id="user1",
        task_id="task1",
        project_id="project1",
        canonical_id="agent1",
        assignee_written="agent1",
        reason="test reason",
        assigned_at=1000.0,
        lease_expires_at=2000.0,
    )
    # Insert an expired lease for agent1 (state will be 'expired' after resolution, but we insert as pending and then update?)
    # For simplicity, we insert a pending lease and then manually update its state to expired? 
    # Instead, we can insert a pending lease and then an expired lease by setting state in the insert? 
    # But insert_lease only inserts with state='pending'. So we need another way to insert an expired row.
    # We'll use the store's _db to directly insert an expired row for the same agent.
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired-id",
                "user1",
                "task2",
                "project1",
                "agent1",
                "agent1",
                "expired",
                "expired reason",
                1000.0,
                2000.0,
                3000.0,
            ),
        )
    # Insert a pending lease for agent2
    await store.insert_lease(
        user_id="user1",
        task_id="task3",
        project_id="project1",
        canonical_id="agent2",
        assignee_written="agent2",
        reason="test reason",
        assigned_at=1000.0,
        lease_expires_at=2000.0,
    )
    # Now call pending_for_agent for agent1
    pending = await store.pending_for_agent("agent1")
    # Should return only the pending lease for agent1 (the first one), not the expired one
    assert len(pending) == 1
    assert pending[0]["task_id"] == "task1"
    assert pending[0]["state"] == "pending"
    # For agent2
    pending2 = await store.pending_for_agent("agent2")
    assert len(pending2) == 1
    assert pending2[0]["task_id"] == "task3"


@pytest.mark.asyncio
async def test_recent_expiries_groups_agents_by_task_since_cutoff(store):
    """recent_expiries returns task_id -> tuple of canonical_ids for expired rows with resolved_at >= since."""
    # Insert an expired lease resolved at time 2000.0
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired1",
                "user1",
                "task1",
                "project1",
                "agent1",
                "agent1",
                "expired",
                "reason1",
                1000.0,
                1500.0,
                2000.0,
            ),
        )
    # Insert another expired lease for the same task, different agent, resolved at 2500.0
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired2",
                "user1",
                "task1",
                "project1",
                "agent2",
                "agent2",
                "expired",
                "reason2",
                1200.0,
                1700.0,
                2500.0,
            ),
        )
    # Insert an expired lease for a different task
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired3",
                "user1",
                "task2",
                "project1",
                "agent1",
                "agent1",
                "expired",
                "reason3",
                1300.0,
                1800.0,
                2200.0,
            ),
        )
    # Insert an expired lease resolved at time 1500.0 (older than cutoff 2000.0)
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired4",
                "user1",
                "task1",
                "project1",
                "agent3",
                "agent3",
                "expired",
                "reason4",
                1400.0,
                1900.0,
                1500.0,
            ),
        )
    # Now call recent_expiries with since=2000.0
    result = await store.recent_expiries(since=2000.0)
    # Expected: task1 -> (agent1, agent2) because agent3's expired row has resolved_at=1500.0 < 2000.0
    # task2 -> (agent1,) because its expired row resolved_at=2200.0 >= 2000.0
    assert set(result.keys()) == {"task1", "task2"}
    assert set(result["task1"]) == {"agent1", "agent2"}
    assert result["task2"] == ("agent1",)


@pytest.mark.asyncio
async def test_expired_counts_48h_ignores_older_rows(store):
    """expired_counts_48h returns task_id -> count of expired rows with resolved_at >= now - 48*3600."""
    now = time.time()
    # Insert an expired lease resolved at now - 24*3600 (within 48h)
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired-recent",
                "user1",
                "task1",
                "project1",
                "agent1",
                "agent1",
                "expired",
                "reason",
                1000.0,
                1500.0,
                now - 24*3600,
            ),
        )
    # Insert an expired lease resolved at now - 72*3600 (older than 48h)
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired-old",
                "user1",
                "task1",
                "project1",
                "agent2",
                "agent2",
                "expired",
                "reason",
                1200.0,
                1700.0,
                now - 72*3600,
            ),
        )
    # Insert an expired lease for task2 within 48h
    async with store._tx():
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "expired-task2",
                "user1",
                "task2",
                "project1",
                "agent1",
                "agent1",
                "expired",
                "reason",
                1300.0,
                1800.0,
                now - 12*3600,
            ),
        )
    # Now call expired_counts_48h
    result = await store.expired_counts_48h(now=now)
    # Expected: task1 -> 1 (only the recent one), task2 -> 1
    assert result == {"task1": 1, "task2": 1}


@pytest.mark.asyncio
async def test_stamp_last_assigned_upserts_and_last_assigned_map_reads_it(store):
    """stamp_last_assigned upserts the timestamp, and last_assigned_map reads it."""
    # Initially, no row for agent1 -> last_assigned_map should not have agent1
    mapping = await store.last_assigned_map(["agent1"])
    assert mapping == {}
    # Stamp a timestamp
    await store.stamp_last_assigned("agent1", 1234567.89)
    # Now the map should contain it
    mapping = await store.last_assigned_map(["agent1"])
    assert mapping == {"agent1": 1234567.89}
    # Stamp again (should update)
    await store.stamp_last_assigned("agent1", 9876543.21)
    mapping = await store.last_assigned_map(["agent1"])
    assert mapping == {"agent1": 9876543.21}
    # Ensure other agents are absent
    mapping = await store.last_assigned_map(["agent1", "agent2"])
    assert mapping == {"agent1": 9876543.21}  # agent2 not present


@pytest.mark.asyncio
async def test_wake_agent_with_task_public_name_and_private_alias_are_the_same_function():
    """ wake_agent_with_task and _wake_agent_with_task refer to the same function."""
    from tinyagentos.agent_heartbeat import wake_agent_with_task, _wake_agent_with_task
    assert wake_agent_with_task is _wake_agent_with_task