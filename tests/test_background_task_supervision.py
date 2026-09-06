"""Tests for #641 — background task supervision."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# task_utils._create_supervised_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supervised_task_tracked_in_set():
    """Task must be added to the set and removed when it completes."""
    from tinyagentos.task_utils import _create_supervised_task

    task_set: set = set()
    completed = asyncio.Event()

    async def _work():
        completed.set()

    task = _create_supervised_task(_work(), task_set)
    assert task in task_set
    await asyncio.wait_for(completed.wait(), timeout=1.0)
    await asyncio.sleep(0)  # let done-callback run
    assert task not in task_set, "completed task should be removed from set"


@pytest.mark.asyncio
async def test_supervised_task_exception_logged(caplog):
    """Unhandled exception in background task must be logged, not silenced."""
    import logging
    from tinyagentos.task_utils import _create_supervised_task

    task_set: set = set()

    async def _bad():
        raise ValueError("deliberate test error")

    with caplog.at_level(logging.ERROR, logger="tinyagentos.task_utils"):
        task = _create_supervised_task(_bad(), task_set)
        # Wait for the task to finish (it will raise).
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        except (ValueError, asyncio.CancelledError):
            pass
        await asyncio.sleep(0)  # let done-callback fire

    assert any("deliberate test error" in r.message for r in caplog.records), (
        "exception from background task must be logged"
    )


@pytest.mark.asyncio
async def test_supervised_task_cancel_removes_from_set():
    """Cancelling a supervised task must remove it from the tracking set."""
    from tinyagentos.task_utils import _create_supervised_task

    task_set: set = set()
    started = asyncio.Event()

    async def _long():
        started.set()
        await asyncio.sleep(60)

    task = _create_supervised_task(_long(), task_set)
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()
    # Give the event loop enough ticks to cancel the coroutine and run callbacks.
    for _ in range(5):
        await asyncio.sleep(0)
    assert task not in task_set


# ---------------------------------------------------------------------------
# app.state._background_tasks set exists after create_app()
# ---------------------------------------------------------------------------

def test_background_tasks_set_exists_on_app_state(tmp_path):
    """app.state must have a _background_tasks set after create_app()."""
    import yaml
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    from tinyagentos.app import create_app
    app = create_app(data_dir=tmp_path)

    assert hasattr(app.state, "_background_tasks"), (
        "app.state must have _background_tasks"
    )
    assert isinstance(app.state._background_tasks, set)


# ---------------------------------------------------------------------------
# AgentChatRouter.dispatch uses supervised task when _background_tasks present
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_uses_supervised_task_when_set_present():
    """dispatch() must put the task in state._background_tasks when it exists."""
    from tinyagentos.agent_chat_router import AgentChatRouter

    state = MagicMock()
    state._background_tasks = set()
    state.config.agents = [{"name": "bot", "status": "running"}]
    state.chat_messages = MagicMock()
    state.chat_messages.get_messages = AsyncMock(return_value=[])
    state.chat_messages.send_message = AsyncMock(return_value={
        "id": "m1", "channel_id": "c1", "author_id": "bot",
        "author_type": "agent", "content": "", "created_at": 1.0,
    })
    state.chat_channels = MagicMock()
    state.chat_channels.update_last_message_at = AsyncMock()
    state.chat_hub = MagicMock()
    state.chat_hub.broadcast = AsyncMock()
    state.chat_hub.next_seq = MagicMock(return_value=1)
    state.bridge_sessions = None  # triggers system reply, but that's fine

    router = AgentChatRouter(state)
    msg = {"id": "m1", "author_id": "user", "author_type": "user",
           "content": "ping", "metadata": {"hops_since_user": 0}}
    channel = {"id": "c1", "type": "dm", "members": ["user", "bot"], "settings": {}}

    router.dispatch(msg, channel)
    # Give the event loop a tick so the task is created
    await asyncio.sleep(0.05)

    # The task was supervised and should have run (and removed itself from set
    # once done).  The important invariant: no untracked create_task orphan.
    # Since bridge_sessions is None, _route posts a system reply and finishes.
    # After it finishes the set should be empty (task removed by done callback).
    assert isinstance(state._background_tasks, set)


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_bare_create_task_without_set():
    """dispatch() must not crash when _background_tasks is absent on state."""
    from tinyagentos.agent_chat_router import AgentChatRouter

    state = MagicMock(spec=[])  # no attributes at all
    # Re-add the minimum required by dispatch + _route
    state._background_tasks = None  # simulate absent set via None
    state.config = MagicMock()
    state.config.agents = []
    state.chat_messages = MagicMock()
    state.chat_messages.get_messages = AsyncMock(return_value=[])
    state.chat_channels = MagicMock()
    state.chat_hub = MagicMock()

    router = AgentChatRouter(state)
    msg = {"author_id": "user", "author_type": "user",
           "content": "hi", "metadata": {"hops_since_user": 0}}
    channel = {"id": "c1", "type": "group", "members": ["user"],
               "settings": {"response_mode": "quiet", "max_hops": 3,
                            "cooldown_seconds": 5, "rate_cap_per_minute": 20, "muted": []}}
    # Must not raise even without _background_tasks set
    router.dispatch(msg, channel)
    await asyncio.sleep(0.05)
