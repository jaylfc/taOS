"""Tests for SSE event routing through the REAL _notify_emitter in app.py.

The existing test_event_stream.py uses a local closure that re-implements the
routing rule, so it cannot catch a regression in app.py. These tests drive the
actual emitter wired during app startup and assert on the EventBus side effect.
"""
from __future__ import annotations

import pytest
import yaml

from tinyagentos.app import create_app


def _make_app(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return create_app(data_dir=tmp_path)


@pytest.mark.asyncio
async def test_owner_scoped_event_never_reaches_other_user(tmp_path):
    """An event for user-a must reach user-a's channel and must NOT appear on
    user-b's channel or broadcast."""
    app = _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        bus = app.state.event_bus
        notif_store = app.state.notifications

        user_a_q = await bus.subscribe("user:user-a")
        user_b_q = await bus.subscribe("user:user-b")
        bcast_q = await bus.subscribe("broadcast")

        await notif_store.add("Alice only", "secret msg", user_id="user-a")

        assert user_a_q.get_nowait() is not None
        assert user_b_q.empty()
        assert bcast_q.empty()


@pytest.mark.asyncio
async def test_owner_scoped_event_does_reach_owner(tmp_path):
    """Control: a user-scoped notification must actually arrive on the owner's
    channel. Catches an over-tightened guard that drops user-scoped events."""
    app = _make_app(tmp_path)
    async with app.router.lifespan_context(app):
        bus = app.state.event_bus
        notif_store = app.state.notifications

        user_a_q = await bus.subscribe("user:user-a")

        await notif_store.add("Alice only", "secret msg", user_id="user-a")

        assert user_a_q.get_nowait() is not None
