"""Tests for dispatcher lease reconciliation."""
from __future__ import annotations

import time

import pytest

from tinyagentos.projects.dispatcher_store import DispatcherStore, DispatcherConfig
from tinyagentos.projects.dispatcher import DispatcherService


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
async def store(db_path):
    store = DispatcherStore(db_path)
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def service(store):
    return DispatcherService(store)


@pytest.mark.anyio
async def test_expired_lease_unassigns_and_backs_off_agent(service, store):
    """Assign, advance now past 900s -> assignee NULL, ledger 'expired', agent next_eligible_at > now, audit 'task.unassigned'."""
    user_id = "user1"
    canonical_id = "agent1"
    task_id = "task1"
    project_id = "project1"
    now = time.time()

    # Insert a ledger row with state pending, assignee_written empty (unclaimed)
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ledger1",
            user_id,
            task_id,
            project_id,
            canonical_id,
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            now - 100,  # assigned_at in the past
            now - 200,  # lease_expires_at in the past (so lease is expired)
            None,
        ),
    )
    # Set the agent's backoff to 0 initially
    await store._db.execute(
        """
        INSERT OR REPLACE INTO dispatch_agent_backoff
            (canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at)
        VALUES (?, 0, 0, 0)
        """,
        (canonical_id,),
    )
    await store._db.commit()

    # Set user config to enabled so that the dispatcher is active (though reconcile_user runs even if disabled)
    await store.set_config(
        user_id,
        DispatcherConfig(
            user_id=user_id,
            enabled=True,
            boards=[project_id],
            eligible_agents=[canonical_id],
            lease_seconds=900,  # 15 minutes
        ),
        updated_by=user_id,
    )

    # Run reconcile_user with now set to a time past the lease expiry
    future_now = now + 1000  # well past lease_expires_at (which was now - 200)
    await service.reconcile_user(user_id, future_now)

    # Check the ledger row: state should be expired
    async with store._read(
        "SELECT state, reason, resolved_at FROM dispatch_ledger WHERE id = ?",
        ("ledger1",),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == "expired"
        assert row[1] == "lease expired"
        assert row[2] == future_now  # resolved_at set to now of reconciliation

    # Check the assignee_written is NULL (empty string)
    async with store._read(
        "SELECT assignee_written FROM dispatch_ledger WHERE id = ?",
        ("ledger1",),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == ""

    # Check the agent's backoff: consecutive_expiries should be 1, next_eligible_at should be future_now + min(900 * 2**(0), 21600) = future_now + 900
    async with store._read(
        "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
        (canonical_id,),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == 1
        assert row[1] == future_now + 900


@pytest.mark.anyio
async def test_human_reassign_marks_overridden_and_is_never_undone(service, store):
    """If a human reassigns the task (assignee_written changed to another agent), the state becomes overridden and never goes back."""
    user_id = "user1"
    canonical_id = "agent1"
    new_canonical_id = "agent2"
    task_id = "task1"
    project_id = "project1"
    now = time.time()

    # Insert a ledger row with state pending, assignee_written empty (unclaimed)
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ledger1",
            user_id,
            task_id,
            project_id,
            canonical_id,
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            now - 100,  # assigned_at in the past
            now + 600,  # lease_expires_at in the future (10 minutes from now)
            None,
        ),
    )
    # Set the agent's backoff to 0 initially
    await store._db.execute(
        """
        INSERT OR REPLACE INTO dispatch_agent_backoff
            (canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at)
        VALUES (?, 0, 0, 0)
        """,
        (canonical_id,),
    )
    await store._db.commit()

    # Set user config to enabled
    await store.set_config(
        user_id,
        DispatcherConfig(
            user_id=user_id,
            enabled=True,
            boards=[project_id],
            eligible_agents=[canonical_id, new_canonical_id],
            lease_seconds=900,  # 15 minutes
        ),
        updated_by=user_id,
    )

    # Now, simulate a human reassigning the task to another agent by updating assignee_written
    await store._db.execute(
        """
        UPDATE dispatch_ledger
        SET assignee_written = ?
        WHERE id = ?
        """,
        (new_canonical_id, "ledger1"),
    )
    await store._db.commit()

    # Run reconcile_user at a time when the lease has not expired yet
    await service.reconcile_user(user_id, now + 300)  # 5 minutes later, lease still valid

    # Check the ledger row: state should be overridden
    async with store._read(
        "SELECT state, reason FROM dispatch_ledger WHERE id = ?",
        ("ledger1",),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == "overridden"
        assert row[1] == "human reassigned"

    # Now, advance time past lease expiry and run reconcile_user again
    future_now = now + 1000  # well past lease_expires_at (which was now + 600)
    await service.reconcile_user(user_id, future_now)

    # The state should still be overridden
    async with store._read(
        "SELECT state, reason FROM dispatch_ledger WHERE id = ?",
        ("ledger1",),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == "overridden"
        assert row[1] == "human reassigned"


@pytest.mark.anyio
async def test_claim_marks_claimed_and_resets_backoff(service, store):
    """When an agent claims a task (assignee_written matches canonical_id and state pending), state becomes claimed and backoff is reset."""
    user_id = "user1"
    canonical_id = "agent1"
    task_id = "task1"
    project_id = "project1"
    now = time.time()

    # Insert a ledger row with state pending, assignee_written empty (unclaimed)
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ledger1",
            user_id,
            task_id,
            project_id,
            canonical_id,
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            now - 100,  # assigned_at in the past
            now + 600,  # lease_expires_at in the future (10 minutes from now)
            None,
        ),
    )
    # Set the agent's backoff to 1 initially (to test reset)
    await store._db.execute(
        """
        INSERT OR REPLACE INTO dispatch_agent_backoff
            (canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at)
        VALUES (?, 1, ?, 0)
        """,
        (canonical_id, now + 100),  # next_eligible_at in the future
    )
    await store._db.commit()

    # Set user config to enabled
    await store.set_config(
        user_id,
        DispatcherConfig(
            user_id=user_id,
            enabled=True,
            boards=[project_id],
            eligible_agents=[canonical_id],
            lease_seconds=900,  # 15 minutes
        ),
        updated_by=user_id,
    )

    # Now, simulate the agent claiming the task by setting assignee_written to its canonical_id
    await store._db.execute(
        """
        UPDATE dispatch_ledger
        SET assignee_written = ?
        WHERE id = ?
        """,
        (canonical_id, "ledger1"),
    )
    await store._db.commit()

    # Run reconcile_user at the current time (lease not expired)
    await service.reconcile_user(user_id, now)

    # Check the ledger row: state should be claimed
    async with store._read(
        "SELECT state, reason FROM dispatch_ledger WHERE id = ?",
        ("ledger1",),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == "claimed"
        assert row[1] == ""  # no reason

    # Check the agent's backoff: consecutive_expiries should be 0, next_eligible_at should be 0
    async with store._read(
        "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
        (canonical_id,),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == 0
        assert row[1] == 0


@pytest.mark.anyio
async def test_disabled_user_leases_still_expire(service, store):
    """Even if the user's config is disabled, lease reconciliation should still run and expire leases."""
    user_id = "user1"
    canonical_id = "agent1"
    task_id = "task1"
    project_id = "project1"
    now = time.time()

    # Insert a ledger row with state pending, assignee_written empty (unclaimed)
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ledger1",
            user_id,
            task_id,
            project_id,
            canonical_id,
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            now - 100,  # assigned_at in the past
            now - 200,  # lease_expires_at in the past (so lease is expired)
            None,
        ),
    )
    # Set the agent's backoff to 0 initially
    await store._db.execute(
        """
        INSERT OR REPLACE INTO dispatch_agent_backoff
            (canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at)
        VALUES (?, 0, 0, 0)
        """,
        (canonical_id,),
    )
    await store._db.commit()

    # Set user config to disabled
    await store.set_config(
        user_id,
        DispatcherConfig(
            user_id=user_id,
            enabled=False,  # disabled
            boards=[project_id],
            eligible_agents=[canonical_id],
            lease_seconds=900,  # 15 minutes
        ),
        updated_by=user_id,
    )

    # Run reconcile_user with now set to a time past the lease expiry
    future_now = now + 1000  # well past lease_expires_at (which was now - 200)
    await service.reconcile_user(user_id, future_now)

    # Check the ledger row: state should be expired
    async with store._read(
        "SELECT state, reason, resolved_at FROM dispatch_ledger WHERE id = ?",
        ("ledger1",),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == "expired"
        assert row[1] == "lease expired"
        assert row[2] == future_now  # resolved_at set to now of reconciliation

    # Check the agent's backoff: consecutive_expiries should be 1, next_eligible_at should be future_now + 900
    async with store._read(
        "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
        (canonical_id,),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == 1
        assert row[1] == future_now + 900


@pytest.mark.anyio
async def test_card_paused_after_5_expiries_in_48h_then_eligible_again(service, store):
    """After 5 expiries in 48h, the card is paused (skipped) until the window rolls."""
    user_id = "user1"
    project_id = "project1"
    canonical_id = "agent1"
    now = time.time()

    # We'll create 5 expired ledger rows for the same project_id (card) within the last 48h.
    # Each row will have a different task_id but same project_id.
    # We'll set the lease_expires_at to be in the past so that they are expired.
    # We'll set the assigned_at to be in the past as well.
    # We'll set the state to pending initially, but after reconcile_user they will become expired.
    ledger_ids = []
    for i in range(5):
        ledger_id = f"ledger{i}"
        task_id = f"task{i}"
        ledger_ids.append(ledger_id)
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ledger_id,
                user_id,
                task_id,
                project_id,
                canonical_id,
                "",  # assignee_written empty -> unclaimed
                "pending",
                "",
                now - 100 - i * 100,  # assigned_at in the past, spread out
                now - 200 - i * 100,  # lease_expires_at in the past (so lease is expired)
                None,
            ),
        )
    await store._db.commit()

    # Set the agent's backoff to 0 initially
    await store._db.execute(
        """
        INSERT OR REPLACE INTO dispatch_agent_backoff
            (canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at)
        VALUES (?, 0, 0, 0)
        """,
        (canonical_id,),
    )
    await store._db.commit()

    # Set user config to enabled
    await store.set_config(
        user_id,
        DispatcherConfig(
            user_id=user_id,
            enabled=True,
            boards=[project_id],
            eligible_agents=[canonical_id],
            lease_seconds=900,  # 15 minutes
        ),
        updated_by=user_id,
    )

    # Run reconcile_user for each ledger row to expire them.
    # We'll do it one by one, but we can also do it in a loop.
    for ledger_id in ledger_ids:
        await service.reconcile_user(user_id, now + 1000)  # future time to ensure lease is expired

    # Now, check that each ledger row is expired and the agent's backoff is 5.
    for ledger_id in ledger_ids:
        async with store._read(
            "SELECT state, reason FROM dispatch_ledger WHERE id = ?",
            (ledger_id,),
        ) as cur:
            row = await cur.fetchone()
            assert row[0] == "expired"
            assert row[1] == "lease expired"

    async with store._read(
        "SELECT consecutive_expiries FROM dispatch_agent_backoff WHERE canonical_id = ?",
        (canonical_id,),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == 5

    # Now, we need to check that the card is paused. We'll insert a new pending task for the same card.
    new_task_id = "new_task"
    new_ledger_id = "new_ledger"
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_ledger_id,
            user_id,
            new_task_id,
            project_id,
            canonical_id,
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            now - 100,  # assigned_at in the past
            now + 600,  # lease_expires_at in the future (so lease is not expired)
            None,
        ),
    )
    await store._db.commit()

    # Run tick_user (which includes reconcile_user and then assignment) for the user.
    # We expect that the new task is not assigned because the card is paused.
    # We'll run tick_user at a time when the new task's lease is still valid (now + 50 seconds).
    await service.tick_user(user_id, now + 50)

    # Check that the new task is still pending and assignee_written is still empty (not assigned)
    async with store._read(
        "SELECT state, assignee_written FROM dispatch_ledger WHERE id = ?",
        (new_ledger_id,),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == "pending"
        assert row[1] == ""

    # Now, advance time beyond 48h from the first expiry and verify the card is no longer paused.
    # The first expiry occurred at resolved_at = now + 1000 (from the first reconcile_user call).
    # We'll advance time to now + 1000 + 48*3600 + 1 seconds (so the first expiry is now outside the window)
    future_now = now + 1000 + (48 * 3600) + 1
    # We want the new task's lease to be valid at future_now, so update its lease_expires_at to future_now + 600 (10 minutes in the future)
    await store._db.execute(
        """
        UPDATE dispatch_ledger
        SET lease_expires_at = ?
        WHERE id = ?
        """,
        (future_now + 600, new_ledger_id),
    )
    await store._db.commit()
    # Run tick_user again
    await service.tick_user(user_id, future_now)

    # Now, the card should no longer be paused, so the task should be assigned (if the agent is eligible and not backed off)
    # We'll check that the task is now assigned (assignee_written set to canonical_id) and state is claimed.
    async with store._read(
        "SELECT state, assignee_written FROM dispatch_ledger WHERE id = ?",
        (new_ledger_id,),
    ) as cur:
        row = await cur.fetchone()
        assert row[0] == "claimed"
        assert row[1] == canonical_id


@pytest.mark.anyio
async def test_backoff_exponential_capped_at_6h(service, store):
    """Backoff increases exponentially with each expiry but is capped at 6 hours (21600 seconds)."""
    user_id = "user1"
    canonical_id = "agent1"
    project_id = "project1"
    now = time.time()
    lease_seconds = 900

    # We'll simulate multiple expiries by repeatedly creating new pending ledger rows and running reconcile_user.
    # We'll do up to the point where the backoff caps at 6h.

    # Let's do 10 expiries to see the cap.
    for i in range(10):
        # Advance time so that the lease is expired (we'll set the lease_expires_at to be in the past by 200 seconds relative to the current now)
        # We'll use a increasing now for each iteration to simulate time passing.
        iteration_now = now + i * (lease_seconds + 100)  # each iteration, we move forward by lease_seconds + 100s

        # Insert a new ledger row with state pending, assignee_written empty (unclaimed) for this iteration
        iter_ledger_id = f"ledger{i}"
        iter_task_id = f"task{i}"
        await store._db.execute(
            """
            INSERT INTO dispatch_ledger
                (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                iter_ledger_id,
                user_id,
                iter_task_id,
                project_id,
                canonical_id,
                "",  # assignee_written empty -> unclaimed
                "pending",
                "",
                iteration_now - 100,  # assigned_at in the past
                iteration_now - 200,  # lease_expires_at in the past (so lease is expired)
                None,
            ),
        )
        await store._db.commit()

        # Run reconcile_user at iteration_now for this ledger row
        await service.reconcile_user(user_id, iteration_now)

        # Check the agent's backoff
        async with store._read(
            "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
            (canonical_id,),
        ) as cur:
            row = await cur.fetchone()
            consecutive_expiries = row[0]
            next_eligible_at = row[1]

        # Calculate expected backoff: lease_seconds * 2**(consecutive_expiries-1), capped at 21600
        expected_backoff = lease_seconds * (2 ** (consecutive_expiries - 1))
        if expected_backoff > 21600:
            expected_backoff = 21600
        expected_next_eligible_at = iteration_now + expected_backoff

        assert consecutive_expiries == i + 1
        assert next_eligible_at == expected_next_eligible_at

    # After 10 expiries, the backoff should be capped at 6h
    # Let's do one more expiry to see if it stays capped
    iteration_now = now + 11 * (lease_seconds + 100)
    iter_ledger_id = f"ledger{11}"
    iter_task_id = f"task{11}"
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            iter_ledger_id,
            user_id,
            iter_task_id,
            project_id,
            canonical_id,
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            iteration_now - 100,  # assigned_at in the past
            iteration_now - 200,  # lease_expires_at in the past (so lease is expired)
            None,
        ),
    )
    await store._db.commit()
    await service.reconcile_user(user_id, iteration_now)

    async with store._read(
        "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
        (canonical_id,),
    ) as cur:
        row = await cur.fetchone()
        consecutive_expiries = row[0]
        next_eligible_at = row[1]

        expected_backoff = 21600  # capped
        expected_next_eligible_at = iteration_now + expected_backoff

        assert consecutive_expiries == 11
        assert next_eligible_at == expected_next_eligible_at


@pytest.mark.anyio
async def test_reexpired_card_goes_to_other_agent_first(service, store):
    """If a card expires and is assigned to the same agent again, it should go to another agent first (anti-affinity)."""
    user_id = "user1"
    canonical_id = "agent1"
    other_canonical_id = "agent2"
    project_id = "project1"
    now = time.time()
    lease_seconds = 900

    # First, create a task for agent1 that expires
    task_id = "task1"
    ledger_id = "ledger1"
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ledger_id,
            user_id,
            task_id,
            project_id,
            canonical_id,
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            now - 100,
            now - 200,  # lease expired
            None,
        ),
    )
    await store._db.commit()

    # Set backoff for agent1 to 0
    await store._db.execute(
        """
        INSERT OR REPLACE INTO dispatch_agent_backoff
            (canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at)
        VALUES (?, 0, 0, 0)
        """,
        (canonical_id,),
    )
    await store._db.commit()

    # Set user config with both agents eligible
    await store.set_config(
        user_id,
        DispatcherConfig(
            user_id=user_id,
            enabled=True,
            boards=[project_id],
            eligible_agents=[canonical_id, other_canonical_id],
            lease_seconds=lease_seconds,
        ),
        updated_by=user_id,
    )

    # Run reconcile_user to expire the first task
    future_now = now + 1000
    await service.reconcile_user(user_id, future_now)

    # Now create a new task for the same card (project), still for agent1 (canonical_id)
    new_task_id = "task2"
    new_ledger_id = "ledger2"
    await store._db.execute(
        """
        INSERT INTO dispatch_ledger
            (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_ledger_id,
            user_id,
            new_task_id,
            project_id,
            canonical_id,  # still targeting agent1
            "",  # assignee_written empty -> unclaimed
            "pending",
            "",
            future_now - 100,
            future_now + 600,  # lease not expired
            None,
        ),
    )
    await store._db.commit()

    # Run tick_user - since agent1 has backoff, the task should go to agent2 (if available)
    # But the current assignment logic just assigns to canonical_id if not backed off
    # This test requires the assignment logic to prefer other agents
    # For now, we'll just verify the test can run without error
    await service.tick_user(user_id, future_now + 50)

    # The task should be assigned to agent2 (other_canonical_id) if agent1 is backed off
    async with store._read(
        "SELECT assignee_written FROM dispatch_ledger WHERE id = ?",
        (new_ledger_id,),
    ) as cur:
        row = await cur.fetchone()
        # With anti-affinity, it should go to agent2, not agent1
        assert row[0] == other_canonical_id