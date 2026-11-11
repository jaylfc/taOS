import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio


@pytest.mark.asyncio
async def test_snapshot_create_invokes_incus():
    with patch("tinyagentos.containers._run", new=AsyncMock(return_value=(0, ""))) as run:
        from tinyagentos.containers import snapshot_create
        await snapshot_create("taos-agent-atlas", "pre-update-1")
        cmd = run.call_args.args[0]
        assert "incus" in cmd and "snapshot" in cmd and "pre-update-1" in cmd


@pytest.mark.asyncio
async def test_snapshot_create_uses_long_timeout():
    """snapshot_create must pass SNAPSHOT_TIMEOUT_S to _run for incus snapshot create calls."""
    with patch("tinyagentos.containers._run", new=AsyncMock(return_value=(0, ""))) as run:
        from tinyagentos.containers import snapshot_create, SNAPSHOT_TIMEOUT_S
        await snapshot_create("taos-agent-atlas", "taos-archive-1")
        # Both the initial and retry (if any) calls should use the long timeout.
        # Here we only have one call since it succeeds on first try.
        assert run.call_count == 1
        _, kwargs = run.call_args
        assert kwargs.get("timeout") == SNAPSHOT_TIMEOUT_S
        assert SNAPSHOT_TIMEOUT_S == 1800


@pytest.mark.asyncio
async def test_run_kills_child_on_timeout():
    """_run must kill the child process when asyncio.wait_for times out."""
    from tinyagentos.containers import _run

    # Create a fake process whose communicate() never completes
    fake_proc = MagicMock()
    # communicate() returns a coroutine that never completes
    async def never_completes():
        await asyncio.Event().wait()  # waits forever
    fake_proc.communicate = never_completes
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", return_value=fake_proc) as mock_create:
        with pytest.raises(asyncio.TimeoutError):
            await _run(["sleep", "5"], timeout=0.05)

        # Verify the process was killed and waited on
        fake_proc.kill.assert_called_once()
        fake_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_list_filters_by_prefix(monkeypatch):
    from tinyagentos.containers import snapshot_list
    # CSV-style output — the real command is `incus snapshot list <name> --format csv`.
    csv = "pre-x,2026/04/18 20:00 UTC\nother,2026/04/18 19:00 UTC\n"
    monkeypatch.setattr(
        "tinyagentos.containers._run",
        AsyncMock(return_value=(0, csv)),
    )
    snaps = await snapshot_list("taos-agent-atlas", prefix="pre-")
    assert [s["name"] for s in snaps] == ["pre-x"]


@pytest.mark.asyncio
async def test_snapshot_list_empty_on_nonzero_exit(monkeypatch):
    from tinyagentos.containers import snapshot_list
    monkeypatch.setattr(
        "tinyagentos.containers._run",
        AsyncMock(return_value=(1, "no such container")),
    )
    assert await snapshot_list("taos-agent-missing") == []


@pytest.mark.asyncio
async def test_snapshot_delete_invokes_incus():
    with patch("tinyagentos.containers._run", new=AsyncMock(return_value=(0, ""))) as run:
        from tinyagentos.containers import snapshot_delete
        await snapshot_delete("taos-agent-atlas", "snap-a")
        cmd = run.call_args.args[0]
        assert "delete" in cmd and "snap-a" in cmd
