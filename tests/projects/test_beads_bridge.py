from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from tinyagentos.projects.beads_bridge import BeadsBridge


def _make_bridge(tmp_path: Path, **overrides) -> BeadsBridge:
    project_store = MagicMock()
    project_store.list_projects = AsyncMock(return_value=[])
    project_store.get_project = AsyncMock(return_value={"id": "prj_1", "slug": "demo"})

    task_store = MagicMock()
    task_store.list_tasks = AsyncMock(return_value=[])
    task_store.list_relationships = AsyncMock(return_value=[])
    task_store.claim_task = AsyncMock(return_value=True)
    task_store.release_task = AsyncMock(return_value=True)
    task_store.close_task = AsyncMock(return_value=True)
    task_store.add_comment = AsyncMock(
        return_value={"id": "cmt_1", "task_id": "tsk_a", "author_id": "u", "body": "x"}
    )
    task_store.get_task = AsyncMock(return_value=None)

    channel_store = MagicMock()
    channel_store.list_channels = AsyncMock(return_value=[])

    msg_store = MagicMock()
    msg_store.send_message = AsyncMock(return_value={"id": "msg_1"})

    broker = MagicMock()
    broker.subscribe = AsyncMock(return_value=asyncio.Queue())
    broker.unsubscribe = AsyncMock()

    return BeadsBridge(
        project_store=overrides.get("project_store", project_store),
        task_store=overrides.get("task_store", task_store),
        channel_store=overrides.get("channel_store", channel_store),
        msg_store=overrides.get("msg_store", msg_store),
        broker=overrides.get("broker", broker),
        data_root=tmp_path,
        debounce_seconds=overrides.get("debounce_seconds", 0.05),
    )


@pytest.mark.asyncio
async def test_start_and_stop_idempotent(tmp_path):
    bridge = _make_bridge(tmp_path)
    await bridge.start()
    await bridge.stop()
    # Second stop is a no-op
    await bridge.stop()


@pytest.mark.asyncio
async def test_mark_dirty_adds_to_set(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge.mark_dirty("prj_1")
    assert "prj_1" in bridge._dirty


@pytest.mark.asyncio
async def test_mark_dirty_idempotent(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge.mark_dirty("prj_1")
    bridge.mark_dirty("prj_1")
    bridge.mark_dirty("prj_1")
    assert bridge._dirty == {"prj_1"}


@pytest.mark.asyncio
async def test_writer_drains_dirty_set_after_debounce(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._render_jsonl = AsyncMock()  # type: ignore[assignment]
    await bridge.start()
    bridge.mark_dirty("prj_1")
    await asyncio.sleep(0.2)
    await bridge.stop()
    assert bridge._render_jsonl.await_count >= 1
    assert "prj_1" not in bridge._dirty


import json


def _task_row(**kw):
    base = {
        "id": "tsk_a",
        "project_id": "prj_1",
        "parent_task_id": None,
        "title": "T",
        "body": "",
        "status": "open",
        "priority": 1,
        "labels": [],
        "assignee_id": None,
        "claimed_by": None,
        "claimed_at": None,
        "closed_at": None,
        "closed_by": None,
        "close_reason": None,
        "created_by": "u",
        "created_at": 1000.0,
        "updated_at": 1000.0,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_render_writes_jsonl_with_one_line_per_task(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._task_store.list_tasks = AsyncMock(
        return_value=[
            _task_row(id="tsk_a", title="A"),
            _task_row(id="tsk_b", title="B"),
        ]
    )
    bridge._task_store.list_relationships = AsyncMock(return_value=[])
    bridge._project_store.get_project = AsyncMock(
        return_value={"id": "prj_1", "slug": "demo"}
    )
    await bridge._render_jsonl("prj_1")
    out = tmp_path / "demo" / ".beads" / "tasks.jsonl"
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "tsk_a"
    assert json.loads(lines[1])["id"] == "tsk_b"


@pytest.mark.asyncio
async def test_render_marks_ready_correctly(tmp_path):
    """tsk_a blocks tsk_b: while tsk_a is open, tsk_b is not ready."""
    bridge = _make_bridge(tmp_path)
    bridge._task_store.list_tasks = AsyncMock(
        return_value=[
            _task_row(id="tsk_a", title="A"),
            _task_row(id="tsk_b", title="B"),
        ]
    )

    async def _list_rels(task_id, direction="from"):
        if direction == "from" and task_id == "tsk_a":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"}
            ]
        if direction == "to" and task_id == "tsk_b":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"}
            ]
        return []

    bridge._task_store.list_relationships = AsyncMock(side_effect=_list_rels)
    bridge._project_store.get_project = AsyncMock(
        return_value={"id": "prj_1", "slug": "demo"}
    )
    bridge._task_store.get_task = AsyncMock(
        side_effect=lambda task_id: {
            "tsk_a": _task_row(id="tsk_a", status="open"),
            "tsk_b": _task_row(id="tsk_b", status="open"),
        }.get(task_id)
    )
    await bridge._render_jsonl("prj_1")
    lines = (tmp_path / "demo" / ".beads" / "tasks.jsonl").read_text().splitlines()
    by_id = {json.loads(line)["id"]: json.loads(line) for line in lines}
    assert by_id["tsk_a"]["ready"] is True
    assert by_id["tsk_b"]["ready"] is False


@pytest.mark.asyncio
async def test_render_skips_unknown_project(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._project_store.get_project = AsyncMock(return_value=None)
    await bridge._render_jsonl("prj_missing")
    # No file should have been written
    assert list(tmp_path.rglob("tasks.jsonl")) == []


@pytest.mark.asyncio
async def test_render_uses_atomic_replace(tmp_path):
    """Render must write to a tmp file and os.replace into place."""
    bridge = _make_bridge(tmp_path)
    bridge._task_store.list_tasks = AsyncMock(return_value=[_task_row()])
    bridge._task_store.list_relationships = AsyncMock(return_value=[])
    bridge._project_store.get_project = AsyncMock(
        return_value={"id": "prj_1", "slug": "demo"}
    )
    await bridge._render_jsonl("prj_1")
    beads_dir = tmp_path / "demo" / ".beads"
    # No leftover .tmp file
    assert not list(beads_dir.glob("*.tmp"))
    assert (beads_dir / "tasks.jsonl").exists()


@pytest.mark.asyncio
async def test_render_failure_re_marks_dirty(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._render_jsonl = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[assignment]
    await bridge.start()
    bridge.mark_dirty("prj_1")
    await asyncio.sleep(0.2)
    await bridge.stop()
    # Failed render re-marks the project dirty
    assert "prj_1" in bridge._dirty
