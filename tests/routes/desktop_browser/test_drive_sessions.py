"""Tests for drive_sessions store methods + capability-check wrappers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    from tinyagentos.routes.desktop_browser.store import BrowserStore

    s = BrowserStore(tmp_path / "browser.sqlite3")
    await s.init()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = dict(user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-a")


# ---------------------------------------------------------------------------
# start_drive_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_drive_session_creates_row(store):
    before = datetime.now(timezone.utc)
    await store.start_drive_session(**BASE)
    rows = await store._db.execute_fetchall(
        "SELECT started_at, last_op_at FROM drive_sessions "
        "WHERE user_id=? AND profile_id=? AND tab_id=? AND agent_id=?",
        (BASE["user_id"], BASE["profile_id"], BASE["tab_id"], BASE["agent_id"]),
    )
    assert len(rows) == 1
    started = datetime.fromisoformat(rows[0][0])
    last_op = datetime.fromisoformat(rows[0][1])
    after = datetime.now(timezone.utc)
    assert before <= started <= after
    assert before <= last_op <= after


@pytest.mark.asyncio
async def test_start_drive_session_upsert_resets_timestamps(store):
    await store.start_drive_session(**BASE)
    rows_first = await store._db.execute_fetchall(
        "SELECT started_at FROM drive_sessions "
        "WHERE user_id=? AND profile_id=? AND tab_id=? AND agent_id=?",
        (BASE["user_id"], BASE["profile_id"], BASE["tab_id"], BASE["agent_id"]),
    )
    first_started = rows_first[0][0]

    # Brief sleep so timestamps differ
    await asyncio.sleep(0.02)
    await store.start_drive_session(**BASE)

    rows_second = await store._db.execute_fetchall(
        "SELECT started_at FROM drive_sessions "
        "WHERE user_id=? AND profile_id=? AND tab_id=? AND agent_id=?",
        (BASE["user_id"], BASE["profile_id"], BASE["tab_id"], BASE["agent_id"]),
    )
    # Only one row (UPSERT, not duplicate insert)
    assert len(rows_second) == 1
    # started_at should be refreshed (>= first value)
    assert rows_second[0][0] >= first_started


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,match", [
    ({"user_id": "",   "profile_id": "p1", "tab_id": "t1", "agent_id": "a"}, "user_id"),
    ({"user_id": "u1", "profile_id": "",   "tab_id": "t1", "agent_id": "a"}, "profile_id"),
    ({"user_id": "u1", "profile_id": "p1", "tab_id": "",   "agent_id": "a"}, "tab_id"),
    ({"user_id": "u1", "profile_id": "p1", "tab_id": "t1", "agent_id": ""}, "agent_id"),
])
async def test_start_drive_session_raises_on_empty_param(store, kwargs, match):
    with pytest.raises(ValueError, match=match):
        await store.start_drive_session(**kwargs)


# ---------------------------------------------------------------------------
# bump_drive_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bump_drive_session_returns_true_on_hit(store):
    await store.start_drive_session(**BASE)
    result = await store.bump_drive_session(**BASE)
    assert result is True


@pytest.mark.asyncio
async def test_bump_drive_session_returns_false_on_miss(store):
    result = await store.bump_drive_session(**BASE)
    assert result is False


@pytest.mark.asyncio
async def test_bump_drive_session_updates_last_op_at_not_started_at(store):
    await store.start_drive_session(**BASE)
    rows_before = await store._db.execute_fetchall(
        "SELECT started_at, last_op_at FROM drive_sessions "
        "WHERE user_id=? AND profile_id=? AND tab_id=? AND agent_id=?",
        (BASE["user_id"], BASE["profile_id"], BASE["tab_id"], BASE["agent_id"]),
    )
    started_before = rows_before[0][0]
    last_op_before = rows_before[0][1]

    await asyncio.sleep(0.02)
    await store.bump_drive_session(**BASE)

    rows_after = await store._db.execute_fetchall(
        "SELECT started_at, last_op_at FROM drive_sessions "
        "WHERE user_id=? AND profile_id=? AND tab_id=? AND agent_id=?",
        (BASE["user_id"], BASE["profile_id"], BASE["tab_id"], BASE["agent_id"]),
    )
    started_after = rows_after[0][0]
    last_op_after = rows_after[0][1]

    # started_at unchanged
    assert started_after == started_before
    # last_op_at moved forward
    assert last_op_after >= last_op_before


# ---------------------------------------------------------------------------
# end_drive_session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_end_drive_session_returns_true_on_hit(store):
    await store.start_drive_session(**BASE)
    result = await store.end_drive_session(**BASE)
    assert result is True


@pytest.mark.asyncio
async def test_end_drive_session_returns_false_on_miss(store):
    result = await store.end_drive_session(**BASE)
    assert result is False


# ---------------------------------------------------------------------------
# is_driving
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_driving_true_within_timeout(store):
    await store.start_drive_session(**BASE)
    result = await store.is_driving(**BASE, idle_timeout_s=30.0)
    assert result is True


@pytest.mark.asyncio
async def test_is_driving_false_after_timeout(store):
    await store.start_drive_session(**BASE)
    # Use a very short timeout and let it expire
    await asyncio.sleep(0.1)
    result = await store.is_driving(**BASE, idle_timeout_s=0.05)
    assert result is False


@pytest.mark.asyncio
async def test_is_driving_false_when_no_row(store):
    result = await store.is_driving(**BASE)
    assert result is False


# ---------------------------------------------------------------------------
# prune_expired_drive_sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prune_expired_removes_only_expired(store):
    # Insert a "stale" row directly with a hand-rolled timestamp 100s in the past.
    # This sidesteps wall-clock flakiness on slow CI runners where any sleep-based
    # timing test has a non-trivial chance of slipping out of its window.
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
    await store._db.execute(
        "INSERT INTO drive_sessions "
        "(user_id, profile_id, tab_id, agent_id, started_at, last_op_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("u2", "p1", "t1", "stale-agent", stale_ts, stale_ts),
    )
    await store._db.commit()

    # Fresh session created normally — last_op_at = now.
    await store.start_drive_session(
        user_id="u1", profile_id="p1", tab_id="t1", agent_id="fresh-agent",
    )

    # Use the production default timeout (30s) so the boundary is wide.
    removed = await store.prune_expired_drive_sessions(idle_timeout_s=30.0)
    assert removed == 1  # Only the stale one

    rows = await store._db.execute_fetchall(
        "SELECT agent_id FROM drive_sessions", ()
    )
    agent_ids = [r[0] for r in rows]
    assert "stale-agent" not in agent_ids
    assert "fresh-agent" in agent_ids


# ---------------------------------------------------------------------------
# Multi-user + multi-profile isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_user_isolation_drive_sessions(store):
    await store.start_drive_session(
        user_id="user-a", profile_id="p1", tab_id="t1", agent_id="agent-x",
    )
    result = await store.is_driving(
        user_id="user-b", profile_id="p1", tab_id="t1", agent_id="agent-x",
    )
    assert result is False


@pytest.mark.asyncio
async def test_multi_profile_isolation_drive_sessions(store):
    await store.start_drive_session(
        user_id="u1", profile_id="profile-x", tab_id="t1", agent_id="agent-a",
    )
    result = await store.is_driving(
        user_id="u1", profile_id="profile-y", tab_id="t1", agent_id="agent-a",
    )
    assert result is False


# ---------------------------------------------------------------------------
# check_drive_capability / check_navigate_capability / check_see_cookies_capability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_drive_capability_returns_true_when_granted(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="drive",
    )
    result = await store.check_drive_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host="example.com",
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_drive_capability_returns_false_for_other_permission(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="navigate",
    )
    result = await store.check_drive_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host="example.com",
    )
    assert result is False


@pytest.mark.asyncio
async def test_check_navigate_capability_returns_true_when_granted(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="navigate",
    )
    result = await store.check_navigate_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host="example.com",
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_navigate_capability_returns_false_for_other_permission(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="drive",
    )
    result = await store.check_navigate_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host="example.com",
    )
    assert result is False


@pytest.mark.asyncio
async def test_check_see_cookies_capability_returns_true_when_granted(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="see_cookies",
    )
    result = await store.check_see_cookies_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host="example.com",
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_see_cookies_capability_returns_false_for_other_permission(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="drive",
    )
    result = await store.check_see_cookies_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host="example.com",
    )
    assert result is False
