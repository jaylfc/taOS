from __future__ import annotations
import time
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_monitor import MonitorService, compute_next_interval


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    yield s
    await s.close()


# --- Smart decay logic ---

def test_decay_on_no_change():
    """No change detected: multiply interval by decay_rate, floor at 86400."""
    new_interval = compute_next_interval(
        current_interval=3600,
        decay_rate=1.5,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == int(3600 * 1.5)


def test_reset_on_change():
    """Change detected: reset to base_frequency."""
    new_interval = compute_next_interval(
        current_interval=7200,
        decay_rate=1.5,
        changed=True,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == 3600


def test_floor_at_24_hours():
    """Interval must never exceed 86400 seconds (24 hours floor for the next poll gap)."""
    new_interval = compute_next_interval(
        current_interval=80000,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    assert new_interval == 86400


def test_stop_after_idle_threshold():
    """stop_after_days no longer stops polling — interval is clamped, never None."""
    new_interval = compute_next_interval(
        current_interval=86400 * 29,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
    )
    # Sub-daily source (base_frequency < 86400) caps at 24h; never returns None
    assert new_interval is not None
    assert new_interval > 0
    assert new_interval == 86400


def test_decay_floors_at_30_days():
    """Decay should never push interval beyond 30 days (2592000 seconds)."""
    new_interval = compute_next_interval(
        current_interval=2000000,
        decay_rate=2.0,
        changed=False,
        base_frequency=86400,
        stop_after_days=0,
    )
    assert new_interval is not None
    assert new_interval <= 2592000, f"Interval {new_interval} exceeds 30-day floor"


def test_decay_does_not_stop_automatically():
    """Items should never stop polling — interval stays at floor, never becomes 0."""
    new_interval = compute_next_interval(
        current_interval=2592000,
        decay_rate=2.0,
        changed=False,
        base_frequency=86400,
        stop_after_days=14,
    )
    assert new_interval is not None, "Interval should not be None (stopped)"
    assert new_interval > 0, "Interval should not be zero (stopped)"
    assert new_interval == 2592000, "Interval should stay at 30-day floor"


def test_pinned_item_uses_base_frequency():
    """Pinned items always return base_frequency regardless of change."""
    new_interval = compute_next_interval(
        current_interval=86400,
        decay_rate=2.0,
        changed=False,
        base_frequency=3600,
        stop_after_days=30,
        pinned=True,
    )
    assert new_interval == 3600


# --- Due-for-poll detection ---

@pytest.mark.asyncio
async def test_items_due_for_poll(store):
    """Items whose last_poll + current_interval <= now should be returned as due."""
    # Item with last_poll far in the past
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/abc",
        title="Thread",
        author="u/tester",
        content="text",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 3600, "decay_rate": 1.5, "stop_after_days": 30,
                  "pinned": False, "last_poll": time.time() - 7200, "current_interval": 3600},
    )
    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()
    assert any(d["id"] == item_id for d in due)


@pytest.mark.asyncio
async def test_items_not_due_yet(store):
    """Items polled recently should not appear in due list."""
    item_id = await store.add_item(
        source_type="reddit",
        source_url="https://reddit.com/r/test/comments/xyz",
        title="Recent Thread",
        author="u/tester",
        content="text",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 3600, "decay_rate": 1.5, "stop_after_days": 30,
                  "pinned": False, "last_poll": time.time(), "current_interval": 3600},
    )
    svc = MonitorService(store=store, http_client=AsyncMock())
    due = await svc.get_due_items()
    assert not any(d["id"] == item_id for d in due)


@pytest.mark.asyncio
async def test_poll_item_updates_monitor_config(store):
    """After a poll, last_poll is updated and current_interval reflects decay."""
    item_id = await store.add_item(
        source_type="article",
        source_url="https://example.com/article",
        title="Article",
        author="",
        content="original content",
        summary="summary",
        categories=[],
        tags=[],
        metadata={},
        status="ready",
        monitor={"frequency": 86400, "decay_rate": 2.0, "stop_after_days": 14,
                  "pinned": False, "last_poll": 0, "current_interval": 86400},
    )
    response = AsyncMock()
    response.status_code = 200
    response.text = "original content"  # no change
    response.raise_for_status = AsyncMock()
    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=response)

    svc = MonitorService(store=store, http_client=mock_http)
    await svc.poll_item(item_id)

    item = await store.get_item(item_id)
    assert item["monitor"]["last_poll"] > 0
    # No change -> interval decays
    assert item["monitor"]["current_interval"] == int(86400 * 2.0)
