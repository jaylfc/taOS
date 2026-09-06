import pytest
import pytest_asyncio

from tinyagentos.scheduler import TaskScheduler


@pytest_asyncio.fixture
async def scheduler(tmp_path):
    s = TaskScheduler(tmp_path / "scheduler.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestTaskScheduler:
    async def test_add_and_list_tasks(self, scheduler):
        task_id = await scheduler.add_task("Test Task", "0 * * * *", "echo hello", "agent-1", "A test")
        assert task_id > 0
        tasks = await scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["name"] == "Test Task"
        assert tasks[0]["agent_name"] == "agent-1"
        assert tasks[0]["schedule"] == "0 * * * *"
        assert tasks[0]["enabled"] is True

    async def test_list_tasks_filter_by_agent(self, scheduler):
        await scheduler.add_task("Task A", "0 * * * *", "cmd_a", "agent-1")
        await scheduler.add_task("Task B", "0 * * * *", "cmd_b", "agent-2")
        all_tasks = await scheduler.list_tasks()
        assert len(all_tasks) == 2
        filtered = await scheduler.list_tasks(agent_name="agent-1")
        assert len(filtered) == 1
        assert filtered[0]["name"] == "Task A"

    async def test_update_task(self, scheduler):
        task_id = await scheduler.add_task("Original", "0 * * * *", "cmd")
        await scheduler.update_task(task_id, name="Updated", command="new_cmd")
        tasks = await scheduler.list_tasks()
        assert tasks[0]["name"] == "Updated"
        assert tasks[0]["command"] == "new_cmd"

    async def test_update_task_enabled(self, scheduler):
        task_id = await scheduler.add_task("Task", "0 * * * *", "cmd")
        await scheduler.update_task(task_id, enabled=False)
        tasks = await scheduler.list_tasks()
        assert tasks[0]["enabled"] is False

    async def test_delete_task(self, scheduler):
        task_id = await scheduler.add_task("To Delete", "0 * * * *", "cmd")
        deleted = await scheduler.delete_task(task_id)
        assert deleted is True
        tasks = await scheduler.list_tasks()
        assert len(tasks) == 0

    async def test_delete_nonexistent_task(self, scheduler):
        deleted = await scheduler.delete_task(9999)
        assert deleted is False

    async def test_default_presets_seeded(self, scheduler):
        presets = await scheduler.get_presets()
        assert len(presets) >= 2
        names = {p["name"] for p in presets}
        assert "Daily Memory Maintenance" in names
        assert "Health Checks" in names

    async def test_apply_preset(self, scheduler):
        presets = await scheduler.get_presets()
        maintenance = next(p for p in presets if p["name"] == "Daily Memory Maintenance")
        count = await scheduler.apply_preset(maintenance["id"], "test-agent")
        assert count == 2
        tasks = await scheduler.list_tasks(agent_name="test-agent")
        assert len(tasks) == 2

    async def test_apply_nonexistent_preset(self, scheduler):
        count = await scheduler.apply_preset(9999, "test-agent")
        assert count == 0
