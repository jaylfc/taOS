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


@pytest.mark.asyncio
async def test_backfill_active_marks_every_active_project(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._project_store.list_projects = AsyncMock(
        return_value=[
            {"id": "prj_1", "slug": "a"},
            {"id": "prj_2", "slug": "b"},
        ]
    )
    n = await bridge.backfill_active()
    assert n == 2
    assert bridge._dirty == {"prj_1", "prj_2"}


@pytest.mark.asyncio
async def test_backfill_active_no_projects(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._project_store.list_projects = AsyncMock(return_value=[])
    n = await bridge.backfill_active()
    assert n == 0
    assert bridge._dirty == set()


@pytest.mark.asyncio
async def test_export_now_writes_synchronously(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._task_store.list_tasks = AsyncMock(return_value=[_task_row()])
    bridge._task_store.list_relationships = AsyncMock(return_value=[])
    bridge._project_store.get_project = AsyncMock(
        return_value={"id": "prj_1", "slug": "demo"}
    )
    path = await bridge.export_now("prj_1")
    assert path.exists()
    assert path.name == "tasks.jsonl"


@pytest.mark.asyncio
async def test_export_now_returns_none_for_missing_project(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._project_store.get_project = AsyncMock(return_value=None)
    path = await bridge.export_now("prj_missing")
    assert path is None


@pytest.mark.asyncio
async def test_on_event_claimed_posts_system_message(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_a", "title": "Hello", "status": "claimed"}
    )
    await bridge.on_event(
        "prj_1",
        {"kind": "task.claimed", "payload": {"id": "tsk_a", "claimed_by": "alice"}},
    )
    assert bridge._msg_store.send_message.await_count == 1
    kwargs = bridge._msg_store.send_message.await_args.kwargs
    assert kwargs["channel_id"] == "ch_1"
    assert kwargs["author_id"] == "bridge"
    assert kwargs["author_type"] == "system"
    assert kwargs["content_type"] == "system"
    assert "alice claimed tsk_a" in kwargs["content"]


@pytest.mark.asyncio
async def test_on_event_released_posts_system_message(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_a", "title": "T", "status": "open", "claimed_by": None}
    )
    await bridge.on_event(
        "prj_1", {"kind": "task.released", "payload": {"id": "tsk_a"}}
    )
    assert bridge._msg_store.send_message.await_count == 1
    assert "released tsk_a" in (
        bridge._msg_store.send_message.await_args.kwargs["content"]
    )


@pytest.mark.asyncio
async def test_on_event_closed_posts_system_message(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )
    bridge._task_store.get_task = AsyncMock(
        return_value={
            "id": "tsk_a",
            "title": "T",
            "status": "closed",
            "closed_by": "alice",
            "close_reason": "ship it",
        }
    )
    bridge._task_store.list_relationships = AsyncMock(return_value=[])
    await bridge.on_event(
        "prj_1",
        {"kind": "task.closed", "payload": {"id": "tsk_a", "closed_by": "alice"}},
    )
    # First call is the closed message
    first_call = bridge._msg_store.send_message.await_args_list[0]
    assert "alice closed tsk_a" in first_call.kwargs["content"]
    assert "ship it" in first_call.kwargs["content"]


@pytest.mark.asyncio
async def test_on_event_no_a2a_channel_is_silent(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[])
    bridge._task_store.get_task = AsyncMock(return_value={"id": "tsk_a", "title": "T"})
    await bridge.on_event(
        "prj_1",
        {"kind": "task.claimed", "payload": {"id": "tsk_a", "claimed_by": "alice"}},
    )
    assert bridge._msg_store.send_message.await_count == 0


@pytest.mark.asyncio
async def test_on_event_ignores_unknown_kinds(tmp_path):
    bridge = _make_bridge(tmp_path)
    await bridge.on_event(
        "prj_1", {"kind": "task.created", "payload": {"id": "tsk_a"}}
    )
    assert bridge._msg_store.send_message.await_count == 0


@pytest.mark.asyncio
async def test_on_event_closed_emits_ready_for_unblocked_dependents(tmp_path):
    """When tsk_a closes, tsk_b (which was blocked by tsk_a) should get a
    ⚡ ready system message in the A2A channel."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )

    async def _get_task(task_id):
        if task_id == "tsk_a":
            return {
                "id": "tsk_a",
                "title": "A",
                "status": "closed",
                "closed_by": "alice",
                "close_reason": None,
            }
        if task_id == "tsk_b":
            return {
                "id": "tsk_b",
                "title": "B",
                "status": "open",
                "labels": ["frontend"],
            }
        return None

    bridge._task_store.get_task = AsyncMock(side_effect=_get_task)

    async def _list_rels(task_id, direction="from"):
        # tsk_a blocks tsk_b
        if task_id == "tsk_a" and direction == "from":
            return [
                {
                    "from_task_id": "tsk_a",
                    "to_task_id": "tsk_b",
                    "kind": "blocks",
                }
            ]
        if task_id == "tsk_b" and direction == "to":
            return [
                {
                    "from_task_id": "tsk_a",
                    "to_task_id": "tsk_b",
                    "kind": "blocks",
                }
            ]
        return []

    bridge._task_store.list_relationships = AsyncMock(side_effect=_list_rels)

    await bridge.on_event(
        "prj_1",
        {"kind": "task.closed", "payload": {"id": "tsk_a", "closed_by": "alice"}},
    )

    # Two messages: closed for tsk_a, ready for tsk_b
    assert bridge._msg_store.send_message.await_count == 2
    bodies = [
        c.kwargs["content"] for c in bridge._msg_store.send_message.await_args_list
    ]
    assert any("closed tsk_a" in b for b in bodies)
    assert any("tsk_b ready" in b for b in bodies)


@pytest.mark.asyncio
async def test_on_event_closed_no_ready_when_other_blocker_open(tmp_path):
    """If tsk_b has another open blocker besides the closing tsk_a, no
    ⚡ ready emits."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )

    async def _get_task(task_id):
        if task_id == "tsk_a":
            return {"id": "tsk_a", "title": "A", "status": "closed"}
        if task_id == "tsk_b":
            return {"id": "tsk_b", "title": "B", "status": "open", "labels": []}
        if task_id == "tsk_c":
            return {"id": "tsk_c", "title": "C", "status": "open"}  # still blocking
        return None

    bridge._task_store.get_task = AsyncMock(side_effect=_get_task)

    async def _list_rels(task_id, direction="from"):
        if task_id == "tsk_a" and direction == "from":
            return [{"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"}]
        if task_id == "tsk_b" and direction == "to":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"},
                {"from_task_id": "tsk_c", "to_task_id": "tsk_b", "kind": "blocks"},
            ]
        return []

    bridge._task_store.list_relationships = AsyncMock(side_effect=_list_rels)

    await bridge.on_event(
        "prj_1", {"kind": "task.closed", "payload": {"id": "tsk_a"}}
    )

    bodies = [
        c.kwargs["content"] for c in bridge._msg_store.send_message.await_args_list
    ]
    assert not any("ready" in b for b in bodies)


@pytest.mark.asyncio
async def test_on_event_closed_emits_ready_for_multiple_dependents(tmp_path):
    """Closing tsk_a should emit ⚡ ready for both tsk_b and tsk_c when each
    has only tsk_a as their blocker."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )

    async def _get_task(task_id):
        if task_id == "tsk_a":
            return {
                "id": "tsk_a",
                "title": "A",
                "status": "closed",
                "closed_by": "alice",
            }
        if task_id == "tsk_b":
            return {"id": "tsk_b", "title": "B", "status": "open", "labels": []}
        if task_id == "tsk_c":
            return {"id": "tsk_c", "title": "C", "status": "open", "labels": []}
        return None

    bridge._task_store.get_task = AsyncMock(side_effect=_get_task)

    async def _list_rels(task_id, direction="from"):
        if task_id == "tsk_a" and direction == "from":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"},
                {"from_task_id": "tsk_a", "to_task_id": "tsk_c", "kind": "blocks"},
            ]
        if task_id == "tsk_b" and direction == "to":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"}
            ]
        if task_id == "tsk_c" and direction == "to":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_c", "kind": "blocks"}
            ]
        return []

    bridge._task_store.list_relationships = AsyncMock(side_effect=_list_rels)

    await bridge.on_event(
        "prj_1",
        {"kind": "task.closed", "payload": {"id": "tsk_a", "closed_by": "alice"}},
    )

    calls = bridge._msg_store.send_message.await_args_list
    system_bodies = [
        c.kwargs["content"]
        for c in calls
        if c.kwargs.get("content_type") == "system"
    ]
    # 1 closed + 2 ready
    assert len(system_bodies) == 3
    assert any("closed tsk_a" in b for b in system_bodies)
    assert any("tsk_b ready" in b for b in system_bodies)
    assert any("tsk_c ready" in b for b in system_bodies)


@pytest.mark.asyncio
@pytest.mark.parametrize("dependent_status", ["closed", "cancelled"])
async def test_on_event_closed_no_ready_when_dependent_not_open(
    tmp_path, dependent_status
):
    """If tsk_b is already closed/cancelled, no ⚡ ready emits even though
    its sole blocker tsk_a just closed."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )

    async def _get_task(task_id):
        if task_id == "tsk_a":
            return {"id": "tsk_a", "title": "A", "status": "closed"}
        if task_id == "tsk_b":
            return {"id": "tsk_b", "title": "B", "status": dependent_status}
        return None

    bridge._task_store.get_task = AsyncMock(side_effect=_get_task)

    async def _list_rels(task_id, direction="from"):
        if task_id == "tsk_a" and direction == "from":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"}
            ]
        if task_id == "tsk_b" and direction == "to":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"}
            ]
        return []

    bridge._task_store.list_relationships = AsyncMock(side_effect=_list_rels)

    await bridge.on_event(
        "prj_1", {"kind": "task.closed", "payload": {"id": "tsk_a"}}
    )

    bodies = [
        c.kwargs["content"] for c in bridge._msg_store.send_message.await_args_list
    ]
    # closed message posted, but no ready
    assert any("closed tsk_a" in b for b in bodies)
    assert not any("⚡" in b for b in bodies)


@pytest.mark.asyncio
async def test_on_event_closed_ready_synthesis_handles_missing_dependent(tmp_path):
    """If get_task returns None for the dependent referenced by an outbound
    blocks edge, _announce_newly_ready must skip silently — closed message
    still posts, no ready, no exception."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )

    async def _get_task(task_id):
        if task_id == "tsk_a":
            return {"id": "tsk_a", "title": "A", "status": "closed"}
        # tsk_b lookup returns None (orphan edge)
        return None

    bridge._task_store.get_task = AsyncMock(side_effect=_get_task)

    async def _list_rels(task_id, direction="from"):
        if task_id == "tsk_a" and direction == "from":
            return [
                {"from_task_id": "tsk_a", "to_task_id": "tsk_b", "kind": "blocks"}
            ]
        return []

    bridge._task_store.list_relationships = AsyncMock(side_effect=_list_rels)

    # No exception should escape
    await bridge.on_event(
        "prj_1", {"kind": "task.closed", "payload": {"id": "tsk_a"}}
    )

    bodies = [
        c.kwargs["content"] for c in bridge._msg_store.send_message.await_args_list
    ]
    assert any("closed tsk_a" in b for b in bodies)
    assert not any("ready" in b for b in bodies)


def _a2a_ch():
    return {
        "id": "ch_1",
        "name": "a2a",
        "type": "group",
        "settings": {"kind": "a2a"},
    }


def _msg(content: str, **kw):
    base = {
        "id": "msg_1",
        "channel_id": "ch_1",
        "author_id": "alice",
        "author_type": "agent",
        "content": content,
        "content_type": "text",
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_on_chat_message_skips_system_content_type(tmp_path):
    """Bridge must not loop on its own system messages."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    await bridge.on_chat_message(
        "prj_1", "ch_1", _msg("/claim tsk_abc", content_type="system")
    )
    assert bridge._task_store.claim_task.await_count == 0


@pytest.mark.asyncio
async def test_on_chat_message_skips_non_a2a_channel(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_other",
                "name": "general",
                "type": "topic",
                "settings": {},
            }
        ]
    )
    await bridge.on_chat_message("prj_1", "ch_other", _msg("/claim tsk_abc"))
    assert bridge._task_store.claim_task.await_count == 0


@pytest.mark.asyncio
async def test_on_chat_message_claim_calls_task_store(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    await bridge.on_chat_message("prj_1", "ch_1", _msg("/claim tsk_abc"))
    bridge._task_store.claim_task.assert_awaited_once_with("tsk_abc", "alice")


@pytest.mark.asyncio
async def test_on_chat_message_release_calls_task_store(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    await bridge.on_chat_message("prj_1", "ch_1", _msg("/release tsk_abc"))
    bridge._task_store.release_task.assert_awaited_once_with("tsk_abc", "alice")


@pytest.mark.asyncio
async def test_on_chat_message_close_with_note_calls_task_store(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    await bridge.on_chat_message("prj_1", "ch_1", _msg("/close tsk_abc shipped"))
    bridge._task_store.close_task.assert_awaited_once_with(
        "tsk_abc", closed_by="alice", reason="shipped"
    )


@pytest.mark.asyncio
async def test_on_chat_message_multiple_verbs_processed_in_order(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    await bridge.on_chat_message(
        "prj_1", "ch_1", _msg("/claim tsk_a\n/close tsk_b done")
    )
    bridge._task_store.claim_task.assert_awaited_once_with("tsk_a", "alice")
    bridge._task_store.close_task.assert_awaited_once_with(
        "tsk_b", closed_by="alice", reason="done"
    )


@pytest.mark.asyncio
async def test_on_chat_message_verb_failure_is_silent(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    bridge._task_store.claim_task = AsyncMock(side_effect=ValueError("nope"))
    # Should not raise
    await bridge.on_chat_message("prj_1", "ch_1", _msg("/claim tsk_abc"))


@pytest.mark.asyncio
async def test_on_event_closed_ready_synthesis_failure_does_not_break_close_message(
    tmp_path,
):
    """If list_relationships raises while announcing newly-ready dependents,
    the closed message must already have been posted and no exception
    escapes on_event."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(
        return_value=[
            {
                "id": "ch_1",
                "name": "a2a",
                "type": "group",
                "settings": {"kind": "a2a"},
            }
        ]
    )
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_a", "title": "A", "status": "closed"}
    )
    bridge._task_store.list_relationships = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    # No exception escapes on_event
    await bridge.on_event(
        "prj_1", {"kind": "task.closed", "payload": {"id": "tsk_a"}}
    )

    # Closed message was posted before _announce_newly_ready ran
    calls = bridge._msg_store.send_message.await_args_list
    assert len(calls) == 1
    assert "closed tsk_a" in calls[0].kwargs["content"]


@pytest.mark.asyncio
async def test_on_chat_message_mention_attaches_comment(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_abc", "project_id": "prj_1"}
    )
    await bridge.on_chat_message(
        "prj_1", "ch_1", _msg("seeing tsk_abc explode under load")
    )
    bridge._task_store.add_comment.assert_awaited_once()
    kwargs = bridge._task_store.add_comment.await_args.kwargs
    assert kwargs["task_id"] == "tsk_abc"
    assert kwargs["author_id"] == "alice"
    assert "explode under load" in kwargs["body"]


@pytest.mark.asyncio
async def test_on_chat_message_mention_dedupes_per_message_task(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_abc", "project_id": "prj_1"}
    )
    msg = _msg("ping tsk_abc again", id="msg_dup")
    await bridge.on_chat_message("prj_1", "ch_1", msg)
    await bridge.on_chat_message("prj_1", "ch_1", msg)
    assert bridge._task_store.add_comment.await_count == 1


@pytest.mark.asyncio
async def test_on_chat_message_mention_skips_unknown_task(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    bridge._task_store.get_task = AsyncMock(return_value=None)  # task not found
    await bridge.on_chat_message("prj_1", "ch_1", _msg("ghost tsk_zzz"))
    assert bridge._task_store.add_comment.await_count == 0


@pytest.mark.asyncio
async def test_on_chat_message_mention_skips_cross_project_task(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_abc", "project_id": "prj_OTHER"}
    )
    await bridge.on_chat_message("prj_1", "ch_1", _msg("tsk_abc here"))
    assert bridge._task_store.add_comment.await_count == 0


@pytest.mark.asyncio
async def test_on_chat_message_verb_alone_does_not_double_attach(tmp_path):
    """`/claim tsk_abc` should hit claim_task once and NOT also create a
    comment for the same id (the verb is the action; commenting on top
    would be noise)."""
    bridge = _make_bridge(tmp_path)
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_abc", "project_id": "prj_1"}
    )
    await bridge.on_chat_message("prj_1", "ch_1", _msg("/claim tsk_abc"))
    bridge._task_store.claim_task.assert_awaited_once()
    bridge._task_store.add_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_subscribes_to_broker_for_active_projects(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._project_store.list_projects = AsyncMock(
        return_value=[
            {"id": "prj_1", "slug": "a"},
            {"id": "prj_2", "slug": "b"},
        ]
    )
    await bridge.start()
    await bridge.backfill_active()
    # backfill_active should have subscribed each active project to the broker
    assert bridge._broker.subscribe.await_count == 2
    await bridge.stop()


@pytest.mark.asyncio
async def test_broker_event_triggers_on_event(tmp_path):
    bridge = _make_bridge(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    bridge._broker.subscribe = AsyncMock(return_value=queue)
    bridge._project_store.list_projects = AsyncMock(
        return_value=[{"id": "prj_1", "slug": "a"}]
    )
    bridge._channel_store.list_channels = AsyncMock(return_value=[_a2a_ch()])
    bridge._task_store.get_task = AsyncMock(
        return_value={"id": "tsk_x", "title": "X", "status": "claimed"}
    )

    await bridge.start()
    await bridge.backfill_active()

    # Simulate an event arriving on the broker queue
    from tinyagentos.projects.events import ProjectEvent
    queue.put_nowait(
        ProjectEvent(kind="task.claimed", payload={"id": "tsk_x", "claimed_by": "bob"})
    )
    await asyncio.sleep(0.1)
    await bridge.stop()

    # The subscriber loop should have called on_event, which posts a system msg
    assert bridge._msg_store.send_message.await_count >= 1
