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
