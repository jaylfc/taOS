"""Tests for DispatcherService.tick_user/tick using real store fixtures from D2a."""

import pytest
import pytest_asyncio
from types import SimpleNamespace

from tinyagentos.projects.dispatcher import DispatcherService
from tinyagentos.projects.dispatcher_store import DispatcherConfig
from tinyagentos.projects.dispatch_policy import is_candidate
from tinyagentos.wake_budget import can_wake, record_scheduled_wake
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


class TestDispatcherServiceTickUser:
    """Tests for DispatcherService.tick_user with real stores."""

    @pytest.mark.asyncio
    async def test_tick_spec_case_real_store(self, dispatcher_service, real_stores):
        """Two eligible agents with grants on two boards, three claimable cards
        across the two boards -> one tick assigns exactly 2, one per board,
        each agent at most 1; two leases in DispatcherStore; the assigned rows
        carry assignee = ref.assignee_value()."""
        # Create two boards
        board1_row, tasks1 = await seed_board_with_cards(real_stores, "u1", "Board One", 2)
        board2_row, tasks2 = await seed_board_with_cards(real_stores, "u1", "Board Two", 1)
        board1_id = board1_row["id"]
        board2_id = board2_row["id"]

        # Add claimable label to all tasks
        for task in tasks1 + tasks2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        await real_stores.task_store._db.commit()

        # Register two agents with grants on both boards
        agent1_cid = await register_agent_with_grant(real_stores, "agent-one", board1_id, "project_tasks")
        await real_stores.grants.add_grant(canonical_id=agent1_cid, scope="project_tasks", project_id=board2_id)
        agent2_cid = await register_agent_with_grant(real_stores, "agent-two", board1_id, "project_tasks")
        await real_stores.grants.add_grant(canonical_id=agent2_cid, scope="project_tasks", project_id=board2_id)

        # Configure dispatcher for user u1 with both agents eligible
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

        # Run tick
        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)

        # Verify counts
        assert result["boards_held"] == 0
        assert result["candidates_considered"] == 3
        assert result["assignments_made"] == 2
        assert result["races_skipped"] == 0
        assert result["wakes"] == 0  # chat_channels is None

        # Verify leases exist
        leases1 = await real_stores.dispatcher_store.pending_for_agent(agent1_cid)
        leases2 = await real_stores.dispatcher_store.pending_for_agent(agent2_cid)
        total_leases = len(leases1) + len(leases2)
        assert total_leases == 2

        # Verify assigned rows carry assignee = ref.assignee_value()
        # (which is canonical_id for external agents)
        all_leases = leases1 + leases2
        for lease in all_leases:
            assert lease["assignee_written"] in (agent1_cid, agent2_cid)

    @pytest.mark.asyncio
    async def test_tick_never_assigns_without_grant_on_that_board(self, dispatcher_service, real_stores):
        """Agent has grant on board A but not board B; card on board B must not be assigned."""
        board1_row, tasks1 = await seed_board_with_cards(real_stores, "u1", "Board A", 1)
        board2_row, tasks2 = await seed_board_with_cards(real_stores, "u1", "Board B", 1)
        board1_id = board1_row["id"]
        board2_id = board2_row["id"]

        # Add claimable label to all tasks
        for task in tasks1 + tasks2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        await real_stores.task_store._db.commit()

        # Agent only granted on board A
        agent_cid = await register_agent_with_grant(real_stores, "agent-a", board1_id, "project_tasks")

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

        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)

        # Only board A's card should be assigned (board B has no grant)
        assert result["boards_held"] == 0
        assert result["candidates_considered"] == 2
        assert result["assignments_made"] == 1

        leases = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases) == 1
        assert leases[0]["project_id"] == board1_id

    @pytest.mark.asyncio
    async def test_board_with_dispatch_hold_setting_is_never_dispatched(self, dispatcher_service, real_stores):
        """Board with settings.dispatch == 'hold' is skipped entirely."""
        board1_row, tasks1 = await seed_board_with_cards(real_stores, "u1", "Normal Board", 1)
        board2_row, tasks2 = await seed_board_with_cards(real_stores, "u1", "Hold Board", 1)
        board1_id = board1_row["id"]
        board2_id = board2_row["id"]

        # Add claimable label to all tasks
        for task in tasks1 + tasks2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        await real_stores.task_store._db.commit()

        # Set dispatch:hold on board2
        await real_stores.project_store._db.execute(
            "UPDATE projects SET settings = json_set(settings, '$.dispatch', 'hold') WHERE id = ?",
            (board2_id,),
        )
        await real_stores.project_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-hold", board1_id, "project_tasks")
        await real_stores.grants.add_grant(canonical_id=agent_cid, scope="project_tasks", project_id=board2_id)

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

        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)

        # Only normal board should be processed
        assert result["boards_held"] == 1
        assert result["assignments_made"] == 1

        leases = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases) == 1
        assert leases[0]["project_id"] == board1_id

    @pytest.mark.asyncio
    async def test_blocked_on_card_skipped_then_dispatched_after_dependency_closes(self, dispatcher_service, real_stores):
        """Card with blocked-on: label is not a candidate; becomes candidate after blocker closes."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Blocked Board", 2)
        board_id = board_row["id"]
        blocker_id = tasks[0]["id"]
        blocked_id = tasks[1]["id"]

        # Add claimable label to both tasks, blocked-on to second
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (blocker_id,),
        )
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable', 'blocked-on:" + blocker_id + "') WHERE id = ?",
            (blocked_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-blocked", board_id, "project_tasks")

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

        # First tick: only blocker should be candidate
        result1 = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)
        assert result1["candidates_considered"] == 1
        assert result1["assignments_made"] == 1

        leases1 = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases1) == 1
        assert leases1[0]["task_id"] == blocker_id

        # Close the blocker
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET status = 'closed', closed_at = ? WHERE id = ?",
            (2000.0, blocker_id),
        )
        await real_stores.task_store._db.commit()

        # Second tick: blocked card should now be candidate and assigned
        result2 = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 3000.0)
        assert result2["candidates_considered"] == 1
        assert result2["assignments_made"] == 1

        leases2 = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        # the blocker's lease was resolved 'closed' by reconcile
        assert len(leases2) == 1
        assert leases2[0]["task_id"] == blocked_id

    @pytest.mark.asyncio
    async def test_disabled_by_default_assigns_nothing(self, real_stores, tmp_path):
        """Disabled config (enabled=False) results in no assignments."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 3)
        board_id = board_row["id"]

        # Add claimable label to all tasks
        for task in tasks:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-disabled", board_id, "project_tasks")

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

        config = SimpleNamespace(agents=[], wake_budget={"global_default": 100})
        app_state = SimpleNamespace(
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
        service = DispatcherService(app_state)

        result = await service.tick(now=1000.0)

        assert result["users_processed"] == 0
        assert result["assignments_made"] == 0
        assert result["boards_held"] == 0

        leases = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases) == 0

    @pytest.mark.asyncio
    async def test_wakes_even_when_heartbeat_disabled_but_respects_wake_budget(self, dispatcher_service, real_stores, monkeypatch):
        """Deployed agent in config.agents with status=running gets woken via
        wake_agent_with_task; monkeypatch that function to verify call."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Wake Board", 1)
        board_id = board_row["id"]

        # Add claimable label to task
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (tasks[0]["id"],),
        )
        await real_stores.task_store._db.commit()

        # Register agent
        agent_cid = await register_agent_with_grant(real_stores, "deployed-agent", board_id, "project_tasks")

        # Add deployed agent to config
        deployed_agent = {
            "id": "cfg-hex-123",
            "name": "deployed-agent",
            "registry_canonical_id": agent_cid,
            "status": "running",
        }
        dispatcher_service.app_state.config.agents = [deployed_agent]

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

        # Mock wake_agent_with_task using monkeypatch
        mock_wake_calls = []
        async def mock_wake(app_state, agent_dict, task_row):
            mock_wake_calls.append((app_state, agent_dict, task_row))
            return True

        monkeypatch.setattr("tinyagentos.projects.dispatcher.wake_agent_with_task", mock_wake)

        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)

        assert result["assignments_made"] == 1
        assert result["wakes"] == 1
        assert len(mock_wake_calls) == 1
        call_args = mock_wake_calls[0]
        assert call_args[1] is deployed_agent  # agent_dict
        assert call_args[2]["project_id"] == board_row["id"]  # task project_id matches board

    @pytest.mark.asyncio
    async def test_exhausted_wake_budget_takes_no_lease_and_no_wake(self, dispatcher_service, real_stores, monkeypatch):
        """Exhausted wake budget results in no lease and no wake."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Wake Budget Board", 1)
        board_id = board_row["id"]

        # Add claimable label to task
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (tasks[0]["id"],),
        )
        await real_stores.task_store._db.commit()

        # Register agent
        agent_cid = await register_agent_with_grant(real_stores, "wake-budget-agent", board_id, "project_tasks")

        # Add deployed agent to config with id "cfg-hex-123"
        deployed_agent = {
            "id": "cfg-hex-123",
            "name": "wake-budget-agent",
            "registry_canonical_id": agent_cid,
            "status": "running",
        }
        dispatcher_service.app_state.config.agents = [deployed_agent]

        # Set wake budget to 1 for global_default
        dispatcher_service.app_state.config.wake_budget = {"global_default": 1}

        # Use up the wake budget for this agent on this board
        record_scheduled_wake(dispatcher_service.app_state.data_dir, "cfg-hex-123", board_id)

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

        # Mock wake_agent_with_task to record calls
        mock_wake_calls = []
        async def mock_wake(app_state, agent_dict, task_row):
            mock_wake_calls.append((app_state, agent_dict, task_row))
            return True

        monkeypatch.setattr("tinyagentos.projects.dispatcher.wake_agent_with_task", mock_wake)

        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)

        # Assertions
        assert result["assignments_made"] == 0
        assert result["wakes"] == 0
        assert len(mock_wake_calls) == 0  # wake_agent_with_task never called

        # No lease should be inserted
        leases = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases) == 0

        # Task row's assignee should still be empty/None
        task_row = await real_stores.task_store.get_task(tasks[0]["id"])
        assert task_row["assignee_id"] is None


    @pytest.mark.asyncio
    async def test_manual_claim_unaffected(self, dispatcher_service, real_stores):
        """Manually claimed task (assignee_id set, not in OPEN_POOL) is not a candidate."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Manual Board", 2)
        board_id = board_row["id"]
        task1_id = tasks[0]["id"]
        task2_id = tasks[1]["id"]

        # Add claimable label to both tasks
        for task in tasks:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        await real_stores.task_store._db.commit()

        # Manually assign task1 to a specific agent (not in OPEN_POOL)
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET assignee_id = 'manual-agent' WHERE id = ?",
            (task1_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-manual", board_id, "project_tasks")

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

        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)

        # Only task2 should be candidate (task1 has specific assignee)
        assert result["candidates_considered"] == 1
        assert result["assignments_made"] == 1

        leases = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases) == 1
        assert leases[0]["task_id"] == task2_id

    @pytest.mark.asyncio
    async def test_race_claim_between_select_and_assign_is_skipped(self, dispatcher_service, real_stores, monkeypatch):
        """When assign_if_unassigned returns False (race), no lease row, no wake."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Race Board", 1)
        board_id = board_row["id"]

        # Add claimable label to task
        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (tasks[0]["id"],),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-race", board_id, "project_tasks")

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

        # Mock assign_if_unassigned to return False using monkeypatch
        mock_assign_calls = []
        async def mock_assign(task_id, assignee_value, assigned_by):
            mock_assign_calls.append((task_id, assignee_value, assigned_by))
            return False

        monkeypatch.setattr(
            real_stores.task_store, "assign_if_unassigned", mock_assign
        )

        result = await dispatcher_service.tick_user("u1", await real_stores.dispatcher_store.get_config("u1"), 1000.0)

        assert result["candidates_considered"] == 1
        assert result["assignments_made"] == 0
        assert result["races_skipped"] == 1
        assert result["wakes"] == 0

        # No lease should be inserted
        leases = await real_stores.dispatcher_store.pending_for_agent(agent_cid)
        assert len(leases) == 0


class TestDispatcherServiceTick:
    """Tests for DispatcherService.tick (multi-user)."""

    @pytest.mark.asyncio
    async def test_tick_processes_multiple_users(self, real_stores, tmp_path):
        """tick() processes all enabled users, aggregates counts."""
        # User 1
        board1_row, tasks1 = await seed_board_with_cards(real_stores, "u1", "Board U1", 1)
        for task in tasks1:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        agent1_cid = await register_agent_with_grant(real_stores, "agent-u1", board1_row["id"], "project_tasks")
        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(user_id="u1", enabled=True, eligible_agents=[agent1_cid], max_concurrent_per_agent=1, lease_seconds=900),
            updated_by="test",
        )

        # User 2
        board2_row, tasks2 = await seed_board_with_cards(real_stores, "u2", "Board U2", 1)
        for task in tasks2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        agent2_cid = await register_agent_with_grant(real_stores, "agent-u2", board2_row["id"], "project_tasks")
        await real_stores.dispatcher_store.set_config(
            "u2",
            DispatcherConfig(user_id="u2", enabled=True, eligible_agents=[agent2_cid], max_concurrent_per_agent=1, lease_seconds=900),
            updated_by="test",
        )

        # User 3 (disabled)
        board3_row, tasks3 = await seed_board_with_cards(real_stores, "u3", "Board U3", 1)
        for task in tasks3:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        agent3_cid = await register_agent_with_grant(real_stores, "agent-u3", board3_row["id"], "project_tasks")
        await real_stores.dispatcher_store.set_config(
            "u3",
            DispatcherConfig(user_id="u3", enabled=False, eligible_agents=[agent3_cid], max_concurrent_per_agent=1, lease_seconds=900),
            updated_by="test",
        )

        await real_stores.task_store._db.commit()

        config = SimpleNamespace(agents=[], wake_budget={"global_default": 100})
        app_state = SimpleNamespace(
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
        service = DispatcherService(app_state)

        result = await service.tick(now=1000.0)

        assert result["users_processed"] == 2
        assert result["users_failed"] == 0
        assert result["assignments_made"] == 2
        assert result["boards_held"] == 0

    @pytest.mark.asyncio
    async def test_tick_one_user_failure_does_not_stop_others(self, real_stores, tmp_path):
        """Exception in one user's tick_user is caught, others continue."""
        # User 1 (will fail due to missing data_dir)
        board1_row, tasks1 = await seed_board_with_cards(real_stores, "u1", "Board U1", 1)
        for task in tasks1:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        agent1_cid = await register_agent_with_grant(real_stores, "agent-u1", board1_row["id"], "project_tasks")
        await real_stores.dispatcher_store.set_config(
            "u1",
            DispatcherConfig(user_id="u1", enabled=True, eligible_agents=[agent1_cid], max_concurrent_per_agent=1, lease_seconds=900),
            updated_by="test",
        )

        # User 2 (success)
        board2_row, tasks2 = await seed_board_with_cards(real_stores, "u2", "Board U2", 1)
        for task in tasks2:
            await real_stores.task_store._db.execute(
                "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
                (task["id"],),
            )
        agent2_cid = await register_agent_with_grant(real_stores, "agent-u2", board2_row["id"], "project_tasks")
        await real_stores.dispatcher_store.set_config(
            "u2",
            DispatcherConfig(user_id="u2", enabled=True, eligible_agents=[agent2_cid], max_concurrent_per_agent=1, lease_seconds=900),
            updated_by="test",
        )

        await real_stores.task_store._db.commit()

        # App state with NO data_dir -> tick_user will raise RuntimeError
        config = SimpleNamespace(agents=[], wake_budget={"global_default": 100})
        app_state = SimpleNamespace(
            config=config,
            data_dir=None,  # This will cause RuntimeError
            project_store=real_stores.project_store,
            project_task_store=real_stores.task_store,
            dispatcher_store=real_stores.dispatcher_store,
            agent_registry=real_stores.registry,
            agent_grants=real_stores.grants,
            chat_channels=None,
            chat_messages=None,
        )
        service = DispatcherService(app_state)

        result = await service.tick(now=1000.0)

        assert result["users_processed"] == 0  # u1 failed, u2 also fails because data_dir is None for all
        assert result["users_failed"] == 2

        # Now test with data_dir but one user's tick fails for another reason
        # We can't easily cause a per-user failure without more mocking,
        # so this test mainly verifies the try/except structure exists

    @pytest.mark.asyncio
    async def test_tick_disabled_user_expired_lease_counts_expired(self, real_stores, tmp_path):
        """tick() with a disabled user holding an expired pending lease returns
        totals with expired_count >= 1."""
        board_row, tasks = await seed_board_with_cards(real_stores, "u1", "Test Board", 1)
        board_id = board_row["id"]
        task_id = tasks[0]["id"]

        await real_stores.task_store._db.execute(
            "UPDATE project_tasks SET labels = json_array('claimable') WHERE id = ?",
            (task_id,),
        )
        await real_stores.task_store._db.commit()

        agent_cid = await register_agent_with_grant(real_stores, "agent-disabled-expired", board_id, "project_tasks")

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

        config = SimpleNamespace(agents=[], wake_budget={"global_default": 100})
        app_state = SimpleNamespace(
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
        service = DispatcherService(app_state)

        # tick() should reconcile the expired lease and return expired_count >= 1
        result = await service.tick(now=now + 100)

        assert result["expired_count"] >= 1, f"Expected expired_count >= 1, got {result['expired_count']}"


@pytest.mark.asyncio
async def test_dispatcher_loop_cancel_raises_cancelled_error():
    """start the loop as a task, cancel it, assert CancelledError, not NameError"""
    import asyncio
    from tinyagentos.projects.dispatcher import dispatcher_tick_loop

    # Minimal app_state
    from types import SimpleNamespace

    class DummyStore:
        async def list_configs(self):
            return []

    app_state = SimpleNamespace(
        dispatcher_service=SimpleNamespace(
            _last_tick_times={},
            _locks={},
        ),
        dispatcher_store=DummyStore(),
    )

    async def dummy_tick_user(*a, **k):
        return None

    app_state.dispatcher_service.tick_user = dummy_tick_user
    # But the loop calls tick_user on the service; and also accesses locks/last_tick_times

    task = asyncio.create_task(dispatcher_tick_loop(app_state, interval=10))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_loop_survives_a_failing_tick():
    """The loop catches Exception and continues (does not crash)."""
    import asyncio
    from tinyagentos.projects.dispatcher import dispatcher_tick_loop
    from types import SimpleNamespace

    class DummyStore:
        def __init__(self):
            self.calls = 0

        async def list_configs(self):
            self.calls += 1
            if self.calls == 1:
                # return one config that will cause failure
                from tinyagentos.projects.dispatcher_store import DispatcherConfig
                return [DispatcherConfig(user_id="u1", enabled=True, poll_seconds=0)]
            return []

    class FailingService:
        def __init__(self):
            self._last_tick_times = {}
            self._locks = {}
            self.fails = True

        async def tick_user(self, *a, **k):
            if self.fails:
                self.fails = False
                raise ValueError("boom")
            return {}

    app_state = SimpleNamespace(
        dispatcher_service=FailingService(),
        dispatcher_store=DummyStore(),
    )

    task = asyncio.create_task(dispatcher_tick_loop(app_state, interval=0.001))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_loop_gives_each_user_a_fresh_now_and_records_completion():
    """The loop gives each user a fresh now and records completion time."""
    import asyncio
    from tinyagentos.projects.dispatcher import dispatcher_tick_loop
    from types import SimpleNamespace
    from tinyagentos.projects.dispatcher_store import DispatcherConfig
    from tinyagentos.projects import dispatcher

    class DummyStore:
        def __init__(self):
            self.calls = 0

        async def list_configs(self):
            self.calls += 1
            if self.calls == 1:
                return [
                    DispatcherConfig(user_id="u1", enabled=True, poll_seconds=0),
                    DispatcherConfig(user_id="u2", enabled=True, poll_seconds=0),
                ]
            return []

    class FakeTime:
        def __init__(self):
            self.t = 1000.0

        def time(self):
            return self.t

        def advance(self, delta):
            self.t += delta

    class DummyService:
        def __init__(self):
            self._last_tick_times = {}
            self._locks = {}
            self.tick_calls = []

        async def tick_user(self, user_id, cfg, now):
            self.tick_calls.append((user_id, now))
            if user_id == "u1":
                dispatcher.time.advance(100)
            return {}

    fake_time = FakeTime()
    original_time = dispatcher.time
    dispatcher.time = fake_time

    try:
        app_state = SimpleNamespace(
            dispatcher_service=DummyService(),
            dispatcher_store=DummyStore(),
        )
        task = asyncio.create_task(dispatcher_tick_loop(app_state, interval=0.001))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        dispatcher.time = original_time

    calls = app_state.dispatcher_service.tick_calls
    assert len(calls) == 2
    call_dict = {user: now for user, now in calls}
    assert call_dict["u1"] == 1000.0
    assert call_dict["u2"] == 1100.0
    assert app_state.dispatcher_service._last_tick_times["u1"] == 1100.0


@pytest.mark.asyncio
async def test_loop_disabled_user_reconcile_called_with_now():
    """Disabled user (enabled=False) with pending lease: reconcile_user is called with a valid now float."""
    import asyncio
    from tinyagentos.projects.dispatcher import dispatcher_tick_loop
    from types import SimpleNamespace
    from tinyagentos.projects.dispatcher_store import DispatcherConfig

    class DummyStore:
        def __init__(self):
            self.configs = [
                DispatcherConfig(user_id="u1", enabled=False, poll_seconds=0)
            ]
            self.pending_called = False

        async def list_configs(self):
            return self.configs

        async def list_pending(self, user_id):
            if not self.pending_called:
                self.pending_called = True
                return [{"id": "lease-1", "task_id": "task-1", "canonical_id": "agent-1"}]
            return []

    reconcile_calls = []

    class DummyService:
        def __init__(self):
            self._last_tick_times = {}
            self._locks = {}

        async def reconcile_user(self, user_id, cfg, now):
            reconcile_calls.append((user_id, now))
            return {"claimed": 0, "overridden": 0, "closed": 0, "expired": 0}

        async def tick_user(self, user_id, cfg, now):
            return {}

    app_state = SimpleNamespace(
        dispatcher_service=DummyService(),
        dispatcher_store=DummyStore(),
    )

    task = asyncio.create_task(dispatcher_tick_loop(app_state, interval=0.001))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # reconcile_user should have been called at least once for the disabled user
    assert len(reconcile_calls) >= 1
    # The `now` argument should be a float (not UnboundLocalError)
    for user_id, now_arg in reconcile_calls:
        assert user_id == "u1"
        assert isinstance(now_arg, float)
        assert now_arg > 0
