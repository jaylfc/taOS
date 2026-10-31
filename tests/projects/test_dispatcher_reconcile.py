"""Tests for dispatcher reconcile / backoff / card-pause logic (Dispatcherservice S6, tsk-6rhjr3)."""

import pytest
import pytest_asyncio
import uuid
from types import SimpleNamespace

from tinyagentos.projects.dispatcher import DispatcherService
from tinyagentos.projects.dispatcher_store import DispatcherConfig
from tinyagentos.projects.task_store import ProjectTaskStore
from tinyagentos.projects.dispatch_policy import is_candidate
from .conftest import seed_board_with_cards, register_agent_with_grant


@pytest_asyncio.fixture
async def app_state(real_stores, tmp_path):
    """Build app_state with real stores and minimal config for tests."""
    config = SimpleNamespace(
        agents=[],
        wake_budget={"global_default": 100},
    )
    return SimpleNamespace(
        config=config,
        data_dir=tmp_path,
        project_store=real_stores.project_store,
        project_task_store=real_stores.task_store,
        dispatcher_store=real_stores.dispatcher_store,
        agent_registry=real_stores.registry,
        agent_grants=real_stores.grants,
        chat_channels=None,
        chat_messages=None,
    )


@pytest_asyncio.fixture
async def dispatcher_service(app_state):
    return DispatcherService(app_state)


class TestDispatcherReconcile:
    """Tests for dispatcher reconcile_user and backoff / card-pause logic."""

    @pytest.mark.asyncio
    async def test_expired_unclaimed_lease_first_expiry(self, dispatcher_service, real_stores, tmp_path):
        """Case (a): expired unclaimed lease -> card assignee cleared, ledger 'expired', backoff n=1,
        next_eligible_at = now+900; second expiry -> now+1800."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        # Add claimable label
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        # Register agent
        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board_id, "project_tasks")

        # Create a pending lease that is expired (set lease_expires_at in the past)
        # Task must be assigned to the agent (assignee_id = assignee_written) for unassign_if to succeed
        now = 1000.0
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = ?, claimed_by = NULL, status = 'open' WHERE id = ?",
            (agent_cid, task_id),
        )
        await real_stores.task_store._db.commit()

        lease_id = await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id,
            project_id=board_id,
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now - 100,
            lease_expires_at=now - 50,  # expired
        )

        # Configure dispatcher for user u1 with the agent eligible
        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=True,
                eligible_agents=[agent_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        # First tick - should reconcile the expired lease
        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now)
        # reconcile_user should resolve it as 'expired'
        assert "expired" in result or result.get("expired_count", 0) == 1

        # Check the lease state is 'expired' by querying the ledger directly
        async with real_stores.dispatcher_store._read(
            "SELECT state, resolved_at FROM dispatch_ledger WHERE id = ?", (lease_id,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "expired"
        assert row[1] is not None

        # Check backoff was bumped: n=1, next_eligible_at = now + 900
        async with real_stores.dispatcher_store._read(
            "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
            (agent_cid,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 1  # consecutive_expiries
        assert row[1] == now + 900  # next_eligible_at

        # Close the first task to prevent any assignment from the first tick's assignment phase
        # from creating a lease that would expire in the second tick
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET status = 'closed', closed_at = ? WHERE id = ?",
            (now + 100, task_id),
        )
        await real_stores.task_store._db.commit()

        # Second tick with same expired lease scenario - should bump to n=2, next_eligible_at = now+1800
        # (We need a fresh now that is after the first next_eligible_at)
        now2 = now + 2000  # well past the first backoff
        # Re-create the scenario: new task, new lease, task assigned to agent
        board_row2, tasks2 = await seed_board_with_cards(real_stores, "u1", "Test Board 2", 1)
        task_id2 = tasks2[0]["id"]
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id2,),
        )
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = ?, claimed_by = NULL, status = 'open' WHERE id = ?",
            (agent_cid, task_id2),
        )
        await real_stores.task_store._db.commit()

        lease_id2 = await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id2,
            project_id=board_row2["id"],
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now2 - 100,
            lease_expires_at=now2 - 50,  # expired
        )

        result2 = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now2)
        # Check the lease state is 'expired' by querying the ledger directly
        async with real_stores.dispatcher_store._read(
            "SELECT state FROM dispatch_ledger WHERE canonical_id = ? AND task_id = ?",
            (agent_cid, task_id2),
        ) as cur:
            row2 = await cur.fetchone()
        assert row2 is not None
        assert row2[0] == "expired"

        # Backoff should be n=2
        backoff2 = await real_stores.dispatcher_store._db.execute(
            "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
            (agent_cid,),
        )
        row2 = await backoff2.fetchone()
        assert row2[0] == 2  # consecutive_expiries
        assert row2[1] == now2 + 1800  # next_eligible_at = now2 + 900 * 2**(2-1) = now2 + 1800

    @pytest.mark.asyncio
    async def test_agent_claimed_resets_backoff(self, dispatcher_service, real_stores, tmp_path):
        """Case (b): agent claimed -> 'claimed', backoff reset to 0."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board_id, "project_tasks")

        # First: create an expired lease to establish backoff
        now = 1000.0
        await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id,
            project_id=board_id,
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now - 100,
            lease_expires_at=now - 50,
        )

        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=True,
                eligible_agents=[agent_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        # Second tick: the task is now claimed by the agent (claimed_by is set to agent's alias)
        # This should resolve as 'claimed' and reset backoff
        # We need to set up the task as claimed by the agent
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = ?, claimed_by = ?, status = 'claimed' WHERE id = ?",
            (agent_cid, agent_cid, task_id),
        )
        await real_stores.task_store._db.commit()

        # Clear any existing backoff
        await real_stores.dispatcher_store._db.execute(
            "DELETE FROM dispatch_agent_backoff WHERE canonical_id = ?",
            (agent_cid,),
        )
        await real_stores.dispatcher_store._db.commit()

        # Now tick - task is claimed by agent
        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now + 100)
        # Should resolve as 'claimed'
        assert result.get("claimed_count", 0) == 1 or "claimed" in result

        # Backoff should be reset
        backoff = await real_stores.dispatcher_store._db.execute(
            "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
            (agent_cid,),
        )
        row = await backoff.fetchone()
        assert row is not None
        assert row[0] == 0  # consecutive_expiries reset to 0
        assert row[1] == 0  # next_eligible_at reset to 0

    @pytest.mark.asyncio
    async def test_assignee_changed_by_human_overridden(self, dispatcher_service, real_stores, tmp_path):
        """Case (c): assignee changed by a human -> 'overridden', card untouched."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board_id, "project_tasks")

        # Create a pending lease
        now = 1000.0
        await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id,
            project_id=board_id,
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now,
            lease_expires_at=now + 3600,
        )

        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=True,
                eligible_agents=[agent_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        # A human changes the assignee to someone else
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = 'human-agent' WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        # Tick - should resolve as 'overridden' since assignee changed
        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now + 100)
        assert result.get("overridden_count", 0) == 1 or "overridden" in result

        # Lease state should be 'overridden' - query ledger directly since pending_for_agent
        # only returns state='pending' rows
        async with real_stores.dispatcher_store._read(
            "SELECT state, resolved_at FROM dispatch_ledger WHERE task_id = ? AND canonical_id = ?",
            (task_id, agent_cid),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "overridden"

    @pytest.mark.asyncio
    async def test_expired_lease_assignee_changed_by_human_overridden_no_backoff(self, dispatcher_service, real_stores, tmp_path):
        """Case (c) expired variant: EXPIRED pending lease whose card assignee a human changed to another id,
        card still open and unclaimed -> 'overridden', card untouched, no backoff bump."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board_id, "project_tasks")

        # Create a pending lease that is EXPIRED (now > lease_expires_at)
        now = 1000.0
        await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id,
            project_id=board_id,
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now - 100,
            lease_expires_at=now - 50,  # expired
        )

        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=True,
                eligible_agents=[agent_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        # A human changes the assignee to someone else (card still open, unclaimed)
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = 'human-agent' WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        # Tick - should resolve as 'overridden' since assignee changed, NOT 'expired'
        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now)
        assert result.get("overridden_count", 0) == 1 or "overridden" in result
        assert result.get("expired_count", 0) == 0

        # Lease state should be 'overridden' - query ledger directly
        async with real_stores.dispatcher_store._read(
            "SELECT state, resolved_at FROM dispatch_ledger WHERE task_id = ? AND canonical_id = ?",
            (task_id, agent_cid),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "overridden"

        # Card's assignee should still be the human's id (card untouched)
        task = await real_stores.task_store.get_task(task_id)
        assert task["assignee_id"] == "human-agent"

        # Agent should have NO backoff row with consecutive_expiries >= 1
        backoff = await real_stores.dispatcher_store._db.execute(
            "SELECT consecutive_expiries FROM dispatch_agent_backoff WHERE canonical_id = ?",
            (agent_cid,),
        )
        row = await backoff.fetchone()
        # Either no row exists, or consecutive_expiries is 0
        if row is not None:
            assert row[0] == 0

    @pytest.mark.asyncio
    async def test_card_closed(self, dispatcher_service, real_stores, tmp_path):
        """Case (d): card closed -> 'closed'."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board_id, "project_tasks")

        # Create a pending lease
        now = 1000.0
        await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id,
            project_id=board_id,
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now,
            lease_expires_at=now + 3600,
        )

        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=True,
                eligible_agents=[agent_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        # Close the task
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET status = 'closed', closed_at = ? WHERE id = ?",
            (2000.0, task_id),
        )
        await real_stores.task_store._db.commit()

        # Tick - should resolve as 'closed' since task status is not open/claimed
        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now + 100)
        assert result.get("closed_count", 0) == 1 or "closed" in result

        # Lease state should be 'closed' - query ledger directly since pending_for_agent
        # only returns state='pending' rows
        async with real_stores.dispatcher_store._read(
            "SELECT state, resolved_at FROM dispatch_ledger WHERE task_id = ? AND canonical_id = ?",
            (task_id, agent_cid),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "closed"
        assert row[1] is not None

    @pytest.mark.asyncio
    async def test_backed_off_agent_no_assignment_next_tick(self, dispatcher_service, real_stores, tmp_path):
        """Case (e): backed-off agent receives no assignment on the next tick while another eligible agent does."""
        # Two boards, two agents
        board1_row, tasks1 = await seed_board_with_cards(real_stores, "u1", "Board One", 1)
        board2_row, tasks2 = await seed_board_with_cards(real_stores, "u1", "Board Two", 1)
        board1_id = board1_row["id"]
        board2_id = board2_row["id"]

        for task in tasks1 + tasks2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        await real_stores.task_store._db.commit()

        # Register two agents
        agent1_cid = await register_agent_with_grant(real_stores, "agent-a", board1_id, "project_tasks")
        agent2_cid = await register_agent_with_grant(real_stores, "agent-b", board2_id, "project_tasks")

        # Add grant for agent-b on both boards
        await real_stores.grants.add_grant(canonical_id=agent2_cid, scope="project_tasks", project_id=board1_id)

        # Configure dispatcher with both agents
        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=True,
                eligible_agents=[agent1_cid, agent2_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        now = 1000.0

        # First tick: assign agent-a on board1, agent-b on board2
        result1 = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now)
        assert result1["assignments_made"] == 2

        # Now manually back off agent-a by bumping its backoff
        await real_stores.dispatcher_store._db.execute(
            """
            INSERT OR REPLACE INTO dispatch_agent_backoff (canonical_id, consecutive_expiries, next_eligible_at, last_assigned_at)
            VALUES (?, 1, ?, ?)
            """,
            (agent1_cid, now + 900, now),
        )
        await real_stores.dispatcher_store._db.commit()

        # Close the first tasks so agents' load resets
        for task in tasks1 + tasks2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET status = 'closed', closed_at = ? WHERE id = ?",
                (now + 100, task["id"]),
            )
        await real_stores.task_store._db.commit()

        # Create new claimable tasks for the second tick
        board1_row2, tasks1_2 = await seed_board_with_cards(real_stores, "u1", "Board One Second", 1)
        board2_row2, tasks2_2 = await seed_board_with_cards(real_stores, "u1", "Board Two Second", 1)
        board1_id_2 = board1_row2["id"]
        board2_id_2 = board2_row2["id"]
        for task in tasks1_2 + tasks2_2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        await real_stores.task_store._db.commit()

        # Add grants for agents on new boards
        await real_stores.grants.add_grant(canonical_id=agent1_cid, scope="project_tasks", project_id=board1_id_2)
        await real_stores.grants.add_grant(canonical_id=agent1_cid, scope="project_tasks", project_id=board2_id_2)
        await real_stores.grants.add_grant(canonical_id=agent2_cid, scope="project_tasks", project_id=board1_id_2)
        await real_stores.grants.add_grant(canonical_id=agent2_cid, scope="project_tasks", project_id=board2_id_2)

        # Second tick at a time BEFORE backed_off_until: agent-a should be backed off, agent-b should still get a lease
        now2 = now + 200  # before backed_off_until (now + 900)
        result2 = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now2)
        # agent-b should get an assignment
        assert result2["assignments_made"] >= 1
        # agent-a should be counted as backed off
        assert result2["agents_backed_off"] >= 1

        # Check that agent-a has NO new pending lease (back-off took effect)
        leases_a = await real_stores.dispatcher_store.pending_for_agent(agent1_cid)
        assert len(leases_a) == 0, "Backed-off agent should not receive new lease"

        # Verify backoff entry still exists with consecutive_expiries >= 1
        async with real_stores.dispatcher_store._read(
            "SELECT consecutive_expiries, next_eligible_at FROM dispatch_agent_backoff WHERE canonical_id = ?",
            (agent1_cid,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] >= 1
        assert row[1] > now2

    @pytest.mark.asyncio
    async def test_card_expiry_cap_5_expired_not_assigned(self, dispatcher_service, real_stores, tmp_path):
        """Case (f): card with 5 'expired' rows in 48h is not assigned, still status open;
        with 4 it is."""
        import uuid

        # One board with one task
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Board One", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        # Register one agent
        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board_id, "project_tasks")

        # Create 5 expired leases on the task (simulating 5 expiries in 48h)
        now = 1000.0

        # First, create a pending lease that will be reconciled
        lease_id = await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id,
            project_id=board_id,
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now - 100,
            lease_expires_at=now - 50,  # expired
        )

        # Set up the task as assigned but NOT claimed (so it's a candidate after unassign)
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = ?, claimed_by = NULL, status = 'open' WHERE id = ?",
            (agent_cid, task_id),
        )
        await real_stores.task_store._db.commit()

        # Now set up 5 'expired' entries in the ledger for this task
        for i in range(5):
            expired_lease_id = uuid.uuid4().hex
            await real_stores.dispatcher_store._db.execute(
                """
                INSERT INTO dispatch_ledger
                    (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expired_lease_id,
                    "u1",
                    task_id,
                    board_id,
                    agent_cid,
                    agent_cid,
                    "expired",
                    "test",
                    now - 100 - i * 100,
                    now - 50 - i * 100,
                    now - 100,
                ),
            )
        await real_stores.dispatcher_store._db.commit()

        # Check expired counts
        expired_counts = await real_stores.dispatcher_store.expired_counts_48h(now)
        assert expired_counts.get(task_id, 0) >= 5

        # Configure dispatcher
        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=True,
                eligible_agents=[agent_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        # First tick - with 5 expiries, card should be paused (not assigned)
        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now + 100)
        assert result["cards_paused"] >= 1

        # Clean up expired entries and create only 4
        await real_stores.dispatcher_store._db.execute(
            "DELETE FROM dispatch_ledger WHERE state = 'expired' AND task_id = ?",
            (task_id,),
        )
        await real_stores.dispatcher_store._db.commit()

        # Create only 4 expired entries directly
        for i in range(4):
            expired_lease_id = uuid.uuid4().hex
            await real_stores.dispatcher_store._db.execute(
                """
                INSERT INTO dispatch_ledger
                    (id, user_id, task_id, project_id, canonical_id, assignee_written, state, reason, assigned_at, lease_expires_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expired_lease_id,
                    "u1",
                    task_id,
                    board_id,
                    agent_cid,
                    agent_cid,
                    "expired",
                    "test",
                    now - 100 - i * 100,
                    now - 50 - i * 100,
                    now - 100,
                ),
            )
        await real_stores.dispatcher_store._db.commit()

        expired_counts2 = await real_stores.dispatcher_store.expired_counts_48h(now)
        assert expired_counts2.get(task_id, 0) == 4

        # Second tick at a later time so backoff has expired
        now2 = now + 2000
        result2 = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), now2)
        assert result2["cards_paused"] == 0
        # The 4-expiry card should get a pending lease
        leases = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases) == 1
        assert leases[0]["task_id"] == task_id

    @pytest.mark.asyncio
    async def test_disabled_user_with_expired_lease_unassigns(self, dispatcher_service, real_stores, tmp_path):
        """Case (g): user with enabled=False and an expired pending lease: one tick() un-assigns it."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board_id, "project_tasks")

        now = 1000.0

        # Task must be assigned to the agent for unassign_if to succeed
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = ?, claimed_by = NULL, status = 'open' WHERE id = ?",
            (agent_cid, task_id),
        )
        await real_stores.task_store._db.commit()

        # Create a pending expired lease
        lease_id = await real_stores.dispatcher_store.insert_lease(
            user_id="u1",
            task_id=task_id,
            project_id=board_id,
            canonical_id=agent_cid,
            assignee_written=agent_cid,
            reason="test",
            assigned_at=now - 100,
            lease_expires_at=now - 50,  # expired
        )

        # Configure dispatcher as DISABLED
        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(
                user_id="u1",
                enabled=False,  # Disabled
                eligible_agents=[agent_cid],
                max_concurrent_per_agent=1,
                lease_seconds=900,
            ),
            updated_by="test",
        )

        # tick() should still run reconcile_user because list_pending is non-empty
        # and un-assign the expired lease
        result = await dispatcher_service.tick(now=now + 100)
        # u1 should be processed (users_processed should include it or the lease should be resolved)
        # The lease state should be 'expired' - query ledger directly since pending_for_agent
        # only returns state='pending' rows
        async with real_stores.dispatcher_store._read(
            "SELECT state, resolved_at FROM dispatch_ledger WHERE task_id = ? AND canonical_id = ?",
            (task_id, agent_cid),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "expired"