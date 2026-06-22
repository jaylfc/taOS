import pytest


@pytest.mark.asyncio
class TestTasksPage:
    async def test_list_tasks_empty(self, client):
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_task(self, client):
        resp = await client.post("/api/tasks", json={
            "name": "Test Job", "schedule": "0 * * * *", "command": "echo hi",
            "agent_name": "test-agent", "description": "A test task",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert data["id"] > 0

    async def test_create_and_list_tasks(self, client):
        await client.post("/api/tasks", json={
            "name": "Job 1", "schedule": "0 * * * *", "command": "cmd1",
        })
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["name"] == "Job 1"

    async def test_filter_tasks_by_agent(self, client):
        await client.post("/api/tasks", json={
            "name": "Agent Task", "schedule": "0 * * * *", "command": "cmd",
            "agent_name": "test-agent",
        })
        await client.post("/api/tasks", json={
            "name": "Global Task", "schedule": "0 * * * *", "command": "cmd",
        })
        resp = await client.get("/api/tasks?agent=test-agent")
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["name"] == "Agent Task"

    async def test_update_task(self, client):
        resp = await client.post("/api/tasks", json={
            "name": "Original", "schedule": "0 * * * *", "command": "old",
        })
        task_id = resp.json()["id"]
        resp = await client.put(f"/api/tasks/{task_id}", json={"name": "Updated"})
        assert resp.status_code == 200

    async def test_get_task(self, client):
        resp = await client.post("/api/tasks", json={
            "name": "Fetch Me", "schedule": "0 * * * *", "command": "echo",
        })
        task_id = resp.json()["id"]
        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Fetch Me"

    async def test_get_nonexistent_task(self, client):
        resp = await client.get("/api/tasks/9999")
        assert resp.status_code == 404

    async def test_get_task_does_not_shadow_presets(self, client):
        resp = await client.get("/api/tasks/presets")
        assert resp.status_code == 200

    async def test_delete_task(self, client):
        resp = await client.post("/api/tasks", json={
            "name": "To Delete", "schedule": "0 * * * *", "command": "rm",
        })
        task_id = resp.json()["id"]
        resp = await client.delete(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    async def test_delete_nonexistent_task(self, client):
        resp = await client.delete("/api/tasks/9999")
        assert resp.status_code == 404

    async def test_toggle_task(self, client):
        resp = await client.post("/api/tasks", json={
            "name": "Toggle Me", "schedule": "0 * * * *", "command": "cmd",
        })
        task_id = resp.json()["id"]
        resp = await client.post(f"/api/tasks/{task_id}/toggle")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        resp = await client.post(f"/api/tasks/{task_id}/toggle")
        assert resp.json()["enabled"] is True

    async def test_list_presets(self, client):
        resp = await client.get("/api/tasks/presets")
        assert resp.status_code == 200
        presets = resp.json()
        assert len(presets) >= 2

    async def test_apply_preset(self, client):
        resp = await client.get("/api/tasks/presets")
        presets = resp.json()
        preset_id = presets[0]["id"]
        resp = await client.post(f"/api/tasks/presets/{preset_id}/apply", json={
            "agent_name": "test-agent",
        })
        assert resp.status_code == 200
        assert resp.json()["tasks_created"] > 0

    async def test_apply_preset_missing_agent(self, client):
        resp = await client.get("/api/tasks/presets")
        presets = resp.json()
        preset_id = presets[0]["id"]
        resp = await client.post(f"/api/tasks/presets/{preset_id}/apply", json={
            "agent_name": "",
        })
        assert resp.status_code == 400
