import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_snapshot_create_invokes_incus():
    with patch("tinyagentos.containers._run", new=AsyncMock(return_value=(0, ""))) as run:
        from tinyagentos.containers import snapshot_create
        await snapshot_create("taos-agent-atlas", "pre-update-1")
        cmd = run.call_args.args[0]
        assert "incus" in cmd and "snapshot" in cmd and "pre-update-1" in cmd


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
