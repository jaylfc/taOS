"""Tests for agent_pins + agent_capabilities store methods on BrowserStore."""
from __future__ import annotations

import time
from datetime import datetime, UTC, timedelta

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
# add_pin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_pin_returns_true_on_first_insert(store):
    result = await store.add_pin(
        user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-a",
    )
    assert result is True


@pytest.mark.asyncio
async def test_add_pin_returns_false_on_duplicate(store):
    await store.add_pin(user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-a")
    result = await store.add_pin(
        user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-a",
    )
    assert result is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,match", [
    ({"user_id": "",   "profile_id": "p1", "tab_id": "t1", "agent_id": "a"}, "user_id"),
    ({"user_id": "u1", "profile_id": "",   "tab_id": "t1", "agent_id": "a"}, "profile_id"),
    ({"user_id": "u1", "profile_id": "p1", "tab_id": "",   "agent_id": "a"}, "tab_id"),
    ({"user_id": "u1", "profile_id": "p1", "tab_id": "t1", "agent_id": ""}, "agent_id"),
])
async def test_add_pin_raises_on_empty_param(store, kwargs, match):
    with pytest.raises(ValueError, match=match):
        await store.add_pin(**kwargs)


# ---------------------------------------------------------------------------
# list_pins_for_tab
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_pins_for_tab_returns_empty_for_unknown_tab(store):
    result = await store.list_pins_for_tab(
        user_id="u1", profile_id="p1", tab_id="nonexistent",
    )
    assert result == []


@pytest.mark.asyncio
async def test_list_pins_for_tab_ordered_by_pinned_at_asc(store):
    await store.add_pin(user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-first")
    time.sleep(0.01)
    await store.add_pin(user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-second")

    pins = await store.list_pins_for_tab(user_id="u1", profile_id="p1", tab_id="t1")

    assert len(pins) == 2
    assert pins[0]["agent_id"] == "agent-first"
    assert pins[1]["agent_id"] == "agent-second"
    assert pins[0]["pinned_at"] <= pins[1]["pinned_at"]


# ---------------------------------------------------------------------------
# Multi-user + multi-profile isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_user_isolation_list_pins_for_user(store):
    await store.add_pin(user_id="a", profile_id="p1", tab_id="t1", agent_id="agent-x")

    result = await store.list_pins_for_user(user_id="b")
    assert result == []


@pytest.mark.asyncio
async def test_multi_user_isolation_list_pins_for_tab(store):
    await store.add_pin(user_id="a", profile_id="p1", tab_id="t1", agent_id="agent-x")

    result = await store.list_pins_for_tab(user_id="b", profile_id="p1", tab_id="t1")
    assert result == []


@pytest.mark.asyncio
async def test_multi_profile_isolation_list_pins_for_tab(store):
    await store.add_pin(user_id="u1", profile_id="profile-x", tab_id="t1", agent_id="agent-a")

    result = await store.list_pins_for_tab(user_id="u1", profile_id="profile-y", tab_id="t1")
    assert result == []


# ---------------------------------------------------------------------------
# count_pins_for_tab
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_pins_for_tab_increments(store):
    assert await store.count_pins_for_tab(user_id="u1", profile_id="p1", tab_id="t1") == 0

    for i in range(5):
        await store.add_pin(
            user_id="u1", profile_id="p1", tab_id="t1", agent_id=f"agent-{i}",
        )
        assert await store.count_pins_for_tab(user_id="u1", profile_id="p1", tab_id="t1") == i + 1


# ---------------------------------------------------------------------------
# delete_pin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_pin_returns_true_on_hit(store):
    await store.add_pin(user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-a")
    result = await store.delete_pin(
        user_id="u1", profile_id="p1", tab_id="t1", agent_id="agent-a",
    )
    assert result is True


@pytest.mark.asyncio
async def test_delete_pin_returns_false_on_miss(store):
    result = await store.delete_pin(
        user_id="u1", profile_id="p1", tab_id="t1", agent_id="nonexistent",
    )
    assert result is False


# ---------------------------------------------------------------------------
# add_capability + list_capabilities
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_capability_upserts_on_same_pk(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="read_dom",
    )
    # Second call with same PK, new permissions — should update
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="read_dom,navigate",
    )

    caps = await store.list_capabilities(user_id="u1", profile_id="p1", agent_id="agent-a")
    assert len(caps) == 1
    assert caps[0]["permissions"] == "read_dom,navigate"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,match", [
    ({"user_id": "",   "profile_id": "p1", "agent_id": "a", "host_pattern": "h", "permissions": "r"}, "user_id"),
    ({"user_id": "u1", "profile_id": "",   "agent_id": "a", "host_pattern": "h", "permissions": "r"}, "profile_id"),
    ({"user_id": "u1", "profile_id": "p1", "agent_id": "",  "host_pattern": "h", "permissions": "r"}, "agent_id"),
    ({"user_id": "u1", "profile_id": "p1", "agent_id": "a", "host_pattern": "", "permissions": "r"}, "host_pattern"),
    ({"user_id": "u1", "profile_id": "p1", "agent_id": "a", "host_pattern": "h", "permissions": ""}, "permissions"),
])
async def test_add_capability_raises_on_empty_param(store, kwargs, match):
    with pytest.raises(ValueError, match=match):
        await store.add_capability(**kwargs)


# ---------------------------------------------------------------------------
# check_capability — pattern matching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_capability_wildcard_subdomain_matches_subdomain(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*.example.com", permissions="read_dom",
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="foo.example.com", permission="read_dom",
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_capability_wildcard_subdomain_matches_root(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*.example.com", permissions="read_dom",
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="example.com", permission="read_dom",
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_capability_wildcard_subdomain_no_match_other(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*.example.com", permissions="read_dom",
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="other.com", permission="read_dom",
    )
    assert result is False


@pytest.mark.asyncio
async def test_check_capability_star_matches_any_host(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*", permissions="read_dom",
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="anything.io", permission="read_dom",
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_capability_expired_grant_returns_false(store):
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*", permissions="read_dom", expires_at=past,
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="example.com", permission="read_dom",
    )
    assert result is False


@pytest.mark.asyncio
async def test_check_capability_future_expiry_returns_true(store):
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*", permissions="read_dom", expires_at=future,
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="example.com", permission="read_dom",
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_capability_permission_not_in_list_returns_false(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*", permissions="read_dom",
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="example.com", permission="drive",
    )
    assert result is False


# ---------------------------------------------------------------------------
# revoke_capability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revoke_capability_returns_true_on_hit(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="example.com", permissions="read_dom",
    )
    result = await store.revoke_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host_pattern="example.com",
    )
    assert result is True


@pytest.mark.asyncio
async def test_revoke_capability_returns_false_on_miss(store):
    result = await store.revoke_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host_pattern="example.com",
    )
    assert result is False


@pytest.mark.asyncio
async def test_revoke_capability_subsequent_check_returns_false(store):
    await store.add_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host_pattern="*", permissions="read_dom",
    )
    await store.revoke_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a", host_pattern="*",
    )
    result = await store.check_capability(
        user_id="u1", profile_id="p1", agent_id="agent-a",
        host="example.com", permission="read_dom",
    )
    assert result is False


# ---------------------------------------------------------------------------
# Multi-user / multi-profile isolation for capabilities
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_user_isolation_capabilities(store):
    await store.add_capability(
        user_id="a", profile_id="p1", agent_id="agent-a",
        host_pattern="*", permissions="read_dom",
    )
    caps = await store.list_capabilities(user_id="b", profile_id="p1")
    assert caps == []


@pytest.mark.asyncio
async def test_multi_profile_isolation_capabilities(store):
    await store.add_capability(
        user_id="u1", profile_id="profile-x", agent_id="agent-a",
        host_pattern="*", permissions="read_dom",
    )
    caps = await store.list_capabilities(user_id="u1", profile_id="profile-y")
    assert caps == []
