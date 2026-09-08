"""Test for: register_worker marks worker ever-seen before fence/stale refusals,
so a later retry skips worker.join notification."""

import pytest
from unittest.mock import AsyncMock

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo


@pytest.mark.asyncio
async def test_join_notified_after_refused_then_accepted():
    """Registration refused then accepted should fire worker.join on first successful registration.

    Bug: _ever_seen.add(info.name) was called before refusal returns, so a refused registration
    would mark the worker as "ever seen", causing the first-time join notification to be skipped
    on retry.
    """
    notif = AsyncMock()
    mgr = ClusterManager(notifications=notif)
    mgr._generation = 1

    w = WorkerInfo(name="testworker", url="http://test:9000", capabilities=["chat"])

    # Fence the manager
    mgr._fenced = True

    # Attempt registration -- should be refused
    ok, reason = await mgr.register_worker(w)
    assert ok is False
    assert reason == "fenced"

    # Unfence the manager
    mgr._fenced = False

    # Register successfully -- should fire worker.join
    ok, reason = await mgr.register_worker(w)
    assert ok is True
    assert reason == ""

    join_calls = [
        c for c in notif.emit_event.await_args_list
        if c.args and c.args[0] == "worker.join"
    ]
    assert len(join_calls) == 1, (
        f"Expected exactly 1 worker.join notification, got {len(join_calls)}"
    )


@pytest.mark.asyncio
async def test_join_notified_once_for_repeat_registration():
    """Control: a genuine re-registration must NOT re-notify with worker.join.

    Ensures the fix doesn't degenerate into notifying on every registration.
    """
    notif = AsyncMock()
    mgr = ClusterManager(notifications=notif)
    mgr._generation = 1

    w = WorkerInfo(name="testworker", url="http://test:9000", capabilities=["chat"])

    # First registration -- should fire worker.join
    await mgr.register_worker(w)

    # Snapshot join call count before second registration
    pre_join_count = sum(
        1 for c in notif.emit_event.await_args_list
        if c.args and c.args[0] == "worker.join"
    )

    # Repeat registration -- should NOT add a new worker.join notification
    await mgr.register_worker(w)

    # Count join calls after second registration
    post_join_count = sum(
        1 for c in notif.emit_event.await_args_list
        if c.args and c.args[0] == "worker.join"
    )

    # Should not have gained a new worker.join notification
    assert post_join_count == pre_join_count, (
        f"Expected {pre_join_count} worker.join notifications after repeat registration, "
        f"got {post_join_count} (gained {post_join_count - pre_join_count})"
    )