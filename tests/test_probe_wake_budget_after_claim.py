"""Probe: wake-budget read surfaces must not lose the charge after the agent claims its task.

See #2601 / tsk-eybrfv.
"""

import pytest


@pytest.mark.asyncio
class TestProbeWakeBudgetAfterClaim:
    async def test_read_surfaces_survive_claim(
        self, client, app, tmp_data_dir, monkeypatch
    ):
        agent = next(a for a in app.state.config.agents if a["name"] == "test-agent")
        agent["id"] = "test-agent"
        agent["status"] = "running"
        app.state.config.server["agent_heartbeat_enabled"] = True

        project = await app.state.project_store.create_project(
            name="proj-claim", slug="proj-claim", created_by="test",
        )
        task = await app.state.project_task_store.create_task(
            project_id=project["id"],
            title="ready task",
            created_by="test",
            assignee_id="test-agent",
        )
        task_id = task["id"]

        async def fake_wake(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "tinyagentos.agent_heartbeat._wake_agent_with_task", fake_wake
        )

        from tinyagentos.agent_heartbeat import _heartbeat_tick

        await _heartbeat_tick(app.state)

        # Pre-claim control: charge is recorded under <agent>:<project_id>.
        resp = await client.get("/api/agents/test-agent/wake-budget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["consumed"] == 1, f"pre-claim control failed: {data}"
        assert data["remaining"] == data["budget"] - 1

        # Claim the task through the real store.
        claimed = await app.state.project_task_store.claim_task(task_id, "test-agent")
        assert claimed is True

        # The task leaves ready_tasks the instant it is claimed.
        ready = await app.state.project_task_store.list_ready_tasks_for_assignee("test-agent")
        assert ready == []

        # Post-claim: both read surfaces must still report the charge.
        resp = await client.get("/api/agents/test-agent/wake-budget")
        assert resp.status_code == 200
        data = resp.json()
        assert data["consumed"] == 1, f"agent read surface lost charge after claim: {data}"
        assert data["remaining"] == data["budget"] - 1

        resp2 = await client.get("/api/observatory/wake-budget")
        assert resp2.status_code == 200
        data2 = resp2.json()
        agent_row = next(a for a in data2["agents"] if a["agent_id"] == "test-agent")
        assert agent_row["consumed"] == 1, f"fleet read surface lost charge after claim: {agent_row}"
        assert agent_row["remaining"] == agent_row["budget"] - 1
