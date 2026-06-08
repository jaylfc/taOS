"""Tests for TOCTOU race condition fixes in job dequeue and lease acquire.

Verifies that:
1. Two concurrent dequeues on separate connections never both claim the same job.
2. Two concurrent lease acquires on separate connections never both succeed.

These tests use two separate JobQueue / LeaseManager instances sharing the same
SQLite file (the real scenario when two coroutines race) and run both operations
concurrently via asyncio.gather.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from tinyagentos.scheduling.job_queue import JobQueue, RESOURCE_CPU, Priority
from tinyagentos.scheduling.leases import LeaseManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def queue(tmp_path):
    q = JobQueue(tmp_path / "jobs.db")
    await q.init()
    yield q
    await q.close()


@pytest_asyncio.fixture
async def queue_pair(tmp_path):
    """Two independent JobQueue instances sharing the same database file."""
    db = tmp_path / "jobs.db"
    q1 = JobQueue(db)
    q2 = JobQueue(db)
    await q1.init()
    await q2.init()
    yield q1, q2
    await q1.close()
    await q2.close()


@pytest_asyncio.fixture
async def lease_pair(tmp_path):
    """Two independent LeaseManager instances sharing the same database file."""
    db = tmp_path / "leases.db"
    m1 = LeaseManager(db)
    m2 = LeaseManager(db)
    await m1.init()
    await m2.init()
    yield m1, m2
    await m1.close()
    await m2.close()


@pytest_asyncio.fixture
async def leases(tmp_path):
    m = LeaseManager(tmp_path / "leases.db")
    await m.init()
    yield m
    await m.close()


# ---------------------------------------------------------------------------
# JobQueue — basic functional tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_and_dequeue(queue):
    job_id = await queue.enqueue("embed", {"text": "hello"}, resource_type=RESOURCE_CPU)
    assert job_id

    job = await queue.dequeue()
    assert job is not None
    assert job["id"] == job_id
    assert job["status"] == "running"


@pytest.mark.asyncio
async def test_dequeue_empty_returns_none(queue):
    result = await queue.dequeue()
    assert result is None


@pytest.mark.asyncio
async def test_dequeue_respects_concurrency_limit(queue):
    # CPU limit is 2; enqueue 3 jobs
    await queue.set_limit(RESOURCE_CPU, 2)
    await queue.enqueue("embed", resource_type=RESOURCE_CPU)
    await queue.enqueue("embed", resource_type=RESOURCE_CPU)
    await queue.enqueue("embed", resource_type=RESOURCE_CPU)

    j1 = await queue.dequeue()
    j2 = await queue.dequeue()
    j3 = await queue.dequeue()  # Should be blocked by limit=2

    assert j1 is not None
    assert j2 is not None
    assert j3 is None  # Limit reached


@pytest.mark.asyncio
async def test_complete_frees_slot_for_next(queue):
    await queue.set_limit(RESOURCE_CPU, 1)
    await queue.enqueue("embed", resource_type=RESOURCE_CPU)
    id2 = await queue.enqueue("embed", resource_type=RESOURCE_CPU)

    j1 = await queue.dequeue()
    assert j1 is not None
    assert await queue.dequeue() is None  # Slot full

    await queue.complete(j1["id"])
    j2 = await queue.dequeue()
    assert j2 is not None
    assert j2["id"] == id2


@pytest.mark.asyncio
async def test_priority_ordering(queue):
    await queue.enqueue("embed", priority=Priority.BACKGROUND, resource_type=RESOURCE_CPU)
    urgent_id = await queue.enqueue("embed", priority=Priority.URGENT, resource_type=RESOURCE_CPU)

    job = await queue.dequeue()
    assert job is not None
    assert job["id"] == urgent_id


@pytest.mark.asyncio
async def test_fail_marks_job_failed(queue):
    job_id = await queue.enqueue("embed", resource_type=RESOURCE_CPU)
    job = await queue.dequeue()
    ok = await queue.fail(job["id"], "boom")
    assert ok
    record = await queue.get_job(job_id)
    assert record["status"] == "failed"
    assert record["error"] == "boom"


@pytest.mark.asyncio
async def test_cancel_pending_job(queue):
    job_id = await queue.enqueue("embed", resource_type=RESOURCE_CPU)
    ok = await queue.cancel(job_id)
    assert ok
    record = await queue.get_job(job_id)
    assert record["status"] == "cancelled"


@pytest.mark.asyncio
async def test_stale_running_jobs_marked_failed_on_init(tmp_path):
    db = tmp_path / "stale.db"
    q = JobQueue(db)
    await q.init()
    job_id = await q.enqueue("embed", resource_type=RESOURCE_CPU)
    await q.dequeue()  # Now status = running
    await q.close()

    # Re-open — stale job should be marked failed
    q2 = JobQueue(db)
    await q2.init()
    record = await q2.get_job(job_id)
    assert record["status"] == "failed"
    assert "stale" in record["error"]
    await q2.close()


# ---------------------------------------------------------------------------
# JobQueue — dequeue atomicity (no double-claim)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_dequeue_claims_each_job_once(queue_pair):
    """Two queues racing to dequeue should not both claim the same job."""
    q1, q2 = queue_pair

    # Set limit=1 so only one job can run at a time
    await q1.set_limit(RESOURCE_CPU, 1)

    job_id = await q1.enqueue("embed", resource_type=RESOURCE_CPU)

    # Both dequeue concurrently
    results = await asyncio.gather(
        q1.dequeue(),
        q2.dequeue(),
    )

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1, (
        f"Expected exactly 1 claim but got {len(claimed)}: {claimed}"
    )
    assert claimed[0]["id"] == job_id
    assert claimed[0]["status"] == "running"


@pytest.mark.asyncio
async def test_concurrent_dequeue_multiple_jobs_within_limit(queue_pair):
    """With limit=2 and two jobs, both dequeues should succeed once each."""
    q1, q2 = queue_pair

    await q1.set_limit(RESOURCE_CPU, 2)
    id1 = await q1.enqueue("embed", resource_type=RESOURCE_CPU)
    id2 = await q1.enqueue("embed", resource_type=RESOURCE_CPU)

    results = await asyncio.gather(
        q1.dequeue(),
        q2.dequeue(),
    )

    claimed_ids = {r["id"] for r in results if r is not None}
    # Both jobs should be claimed, and no duplicates
    assert claimed_ids == {id1, id2}


@pytest.mark.asyncio
async def test_concurrent_dequeue_no_double_claim_under_limit(queue_pair):
    """Two workers racing to dequeue 2 jobs with limit=2 must not double-claim."""
    q1, q2 = queue_pair

    await q1.set_limit(RESOURCE_CPU, 2)
    await q2.set_limit(RESOURCE_CPU, 2)

    id1 = await q1.enqueue("embed", resource_type=RESOURCE_CPU)
    id2 = await q1.enqueue("embed", resource_type=RESOURCE_CPU)

    r1, r2 = await asyncio.gather(q1.dequeue(), q2.dequeue())

    claimed_ids = [r["id"] for r in (r1, r2) if r is not None]
    # No duplicate IDs: each job claimed at most once
    assert len(claimed_ids) == len(set(claimed_ids)), (
        f"Double-claim detected: {claimed_ids}"
    )
    # Both pending jobs should have been picked up
    assert set(claimed_ids) == {id1, id2}


# ---------------------------------------------------------------------------
# LeaseManager — basic functional tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acquire_and_release(leases):
    lease = await leases.acquire("kg:jay", "agent-a")
    assert lease is not None
    assert lease["resource_key"] == "kg:jay"
    assert lease["agent_name"] == "agent-a"
    assert lease["renewed_count"] == 0

    ok = await leases.release("kg:jay", "agent-a")
    assert ok
    assert await leases.check("kg:jay") is None


@pytest.mark.asyncio
async def test_acquire_blocked_by_other_agent(leases):
    await leases.acquire("kg:jay", "agent-a")
    result = await leases.acquire("kg:jay", "agent-b")
    assert result is None


@pytest.mark.asyncio
async def test_same_agent_auto_renews(leases):
    lease1 = await leases.acquire("kg:jay", "agent-a")
    lease2 = await leases.acquire("kg:jay", "agent-a")
    assert lease2 is not None
    assert lease2["renewed_count"] == 1
    assert lease2["expires_at"] > lease1["expires_at"]


@pytest.mark.asyncio
async def test_release_all(leases):
    await leases.acquire("r1", "agent-a")
    await leases.acquire("r2", "agent-a")
    count = await leases.release_all("agent-a")
    assert count == 2
    assert await leases.check("r1") is None
    assert await leases.check("r2") is None


@pytest.mark.asyncio
async def test_is_held_by(leases):
    await leases.acquire("res", "agent-a")
    assert await leases.is_held_by("res", "agent-a") is True
    assert await leases.is_held_by("res", "agent-b") is False


@pytest.mark.asyncio
async def test_renew_count_limit(leases):
    from tinyagentos.scheduling.leases import MAX_RENEW_COUNT
    await leases.acquire("res", "agent-a")
    for _ in range(MAX_RENEW_COUNT):
        result = await leases.acquire("res", "agent-a")
        assert result is not None
    # Next renewal should return None (cap reached)
    result = await leases.acquire("res", "agent-a")
    assert result is None


# ---------------------------------------------------------------------------
# LeaseManager — acquire atomicity (no double-grant)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_acquire_only_one_succeeds(lease_pair):
    """Two managers racing to acquire the same resource — only one should win."""
    m1, m2 = lease_pair

    results = await asyncio.gather(
        m1.acquire("kg:shared", "agent-a"),
        m2.acquire("kg:shared", "agent-b"),
    )

    granted = [r for r in results if r is not None]
    assert len(granted) == 1, (
        f"Expected exactly 1 grant but got {len(granted)}: {granted}"
    )


@pytest.mark.asyncio
async def test_concurrent_acquire_different_resources(lease_pair):
    """Two managers acquiring different resources should both succeed."""
    m1, m2 = lease_pair

    r1, r2 = await asyncio.gather(
        m1.acquire("kg:a", "agent-a"),
        m2.acquire("kg:b", "agent-b"),
    )

    assert r1 is not None
    assert r2 is not None
    assert r1["resource_key"] == "kg:a"
    assert r2["resource_key"] == "kg:b"
