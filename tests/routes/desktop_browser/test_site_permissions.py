"""Tests for BrowserStore site_permissions methods."""
from __future__ import annotations

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
# set_site_permission — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_site_permission_allow(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="example.com", permission="notifications", state="allow",
    )
    rows = await store.list_site_permissions(user_id="u1", profile_id="p1")
    assert len(rows) == 1
    assert rows[0]["host_pattern"] == "example.com"
    assert rows[0]["permission"] == "notifications"
    assert rows[0]["state"] == "allow"


@pytest.mark.asyncio
async def test_set_site_permission_deny(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="evil.com", permission="geolocation", state="deny",
    )
    rows = await store.list_site_permissions(user_id="u1", profile_id="p1")
    assert rows[0]["state"] == "deny"


# ---------------------------------------------------------------------------
# set_site_permission — UPSERT: second write wins
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_site_permission_upsert(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="example.com", permission="camera", state="allow",
    )
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="example.com", permission="camera", state="deny",
    )
    rows = await store.list_site_permissions(user_id="u1", profile_id="p1")
    assert len(rows) == 1
    assert rows[0]["state"] == "deny"


# ---------------------------------------------------------------------------
# set_site_permission — ValueError on empty params (parametrised)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,match", [
    (
        {"user_id": "",   "profile_id": "p1", "host_pattern": "x.com", "permission": "camera",        "state": "allow"},
        "user_id",
    ),
    (
        {"user_id": "u1", "profile_id": "",   "host_pattern": "x.com", "permission": "camera",        "state": "allow"},
        "profile_id",
    ),
    (
        {"user_id": "u1", "profile_id": "p1", "host_pattern": "",      "permission": "camera",        "state": "allow"},
        "host_pattern",
    ),
    (
        {"user_id": "u1", "profile_id": "p1", "host_pattern": "x.com", "permission": "",              "state": "allow"},
        "permission",
    ),
])
async def test_set_site_permission_raises_on_empty_param(store, kwargs, match):
    with pytest.raises(ValueError, match=match):
        await store.set_site_permission(**kwargs)


# ---------------------------------------------------------------------------
# set_site_permission — ValueError on unknown permission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_site_permission_unknown_permission(store):
    with pytest.raises(ValueError, match="unknown permission"):
        await store.set_site_permission(
            user_id="u1", profile_id="p1",
            host_pattern="x.com", permission="unicorn", state="allow",
        )


# ---------------------------------------------------------------------------
# set_site_permission — ValueError on invalid state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_site_permission_invalid_state(store):
    with pytest.raises(ValueError, match="state"):
        await store.set_site_permission(
            user_id="u1", profile_id="p1",
            host_pattern="x.com", permission="camera", state="maybe",
        )


# ---------------------------------------------------------------------------
# list_site_permissions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_site_permissions_empty(store):
    rows = await store.list_site_permissions(user_id="u1", profile_id="p1")
    assert rows == []


@pytest.mark.asyncio
async def test_list_site_permissions_multiple(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1", host_pattern="a.com", permission="camera", state="allow",
    )
    await store.set_site_permission(
        user_id="u1", profile_id="p1", host_pattern="b.com", permission="microphone", state="deny",
    )
    rows = await store.list_site_permissions(user_id="u1", profile_id="p1")
    assert len(rows) == 2
    permissions = {r["permission"] for r in rows}
    assert permissions == {"camera", "microphone"}


# ---------------------------------------------------------------------------
# remove_site_permission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_site_permission_returns_true_on_hit(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="del.com", permission="geolocation", state="allow",
    )
    result = await store.remove_site_permission(
        user_id="u1", profile_id="p1", host_pattern="del.com", permission="geolocation",
    )
    assert result is True
    rows = await store.list_site_permissions(user_id="u1", profile_id="p1")
    assert rows == []


@pytest.mark.asyncio
async def test_remove_site_permission_returns_false_on_miss(store):
    result = await store.remove_site_permission(
        user_id="u1", profile_id="p1", host_pattern="ghost.com", permission="camera",
    )
    assert result is False


# ---------------------------------------------------------------------------
# check_site_permission — pattern matching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_site_permission_wildcard(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="*", permission="notifications", state="allow",
    )
    result = await store.check_site_permission(
        user_id="u1", profile_id="p1", host="any.host.com", permission="notifications",
    )
    assert result == "allow"


@pytest.mark.asyncio
async def test_check_site_permission_subdomain_wildcard_matches_subdomain(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="*.example.com", permission="clipboard-read", state="allow",
    )
    result = await store.check_site_permission(
        user_id="u1", profile_id="p1", host="foo.example.com", permission="clipboard-read",
    )
    assert result == "allow"


@pytest.mark.asyncio
async def test_check_site_permission_subdomain_wildcard_matches_apex(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="*.example.com", permission="clipboard-write", state="deny",
    )
    result = await store.check_site_permission(
        user_id="u1", profile_id="p1", host="example.com", permission="clipboard-write",
    )
    assert result == "deny"


@pytest.mark.asyncio
async def test_check_site_permission_exact_match(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="exact.com", permission="microphone", state="allow",
    )
    result = await store.check_site_permission(
        user_id="u1", profile_id="p1", host="exact.com", permission="microphone",
    )
    assert result == "allow"


@pytest.mark.asyncio
async def test_check_site_permission_exact_does_not_match_subdomain(store):
    await store.set_site_permission(
        user_id="u1", profile_id="p1",
        host_pattern="exact.com", permission="camera", state="allow",
    )
    result = await store.check_site_permission(
        user_id="u1", profile_id="p1", host="sub.exact.com", permission="camera",
    )
    assert result is None


@pytest.mark.asyncio
async def test_check_site_permission_returns_none_when_no_grant(store):
    result = await store.check_site_permission(
        user_id="u1", profile_id="p1", host="example.com", permission="geolocation",
    )
    assert result is None


# ---------------------------------------------------------------------------
# Multi-user / multi-profile isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_site_permission_most_specific_wins(store):
    """Exact host match beats wildcard even when wildcard was set first."""
    # Older wildcard-allow
    await store.set_site_permission(
        user_id="u1", profile_id="p1", host_pattern="*",
        permission="notifications", state="allow",
    )
    # Newer specific-deny
    await store.set_site_permission(
        user_id="u1", profile_id="p1", host_pattern="example.com",
        permission="notifications", state="deny",
    )
    # Should return deny — most specific wins
    assert await store.check_site_permission(
        user_id="u1", profile_id="p1", host="example.com",
        permission="notifications",
    ) == "deny"
    # Other hosts still get the wildcard
    assert await store.check_site_permission(
        user_id="u1", profile_id="p1", host="other.com",
        permission="notifications",
    ) == "allow"


@pytest.mark.asyncio
async def test_check_site_permission_subdomain_wildcard_beats_global_wildcard(store):
    """*.example.com beats * for a matching subdomain."""
    await store.set_site_permission(
        user_id="u1", profile_id="p1", host_pattern="*",
        permission="geolocation", state="allow",
    )
    await store.set_site_permission(
        user_id="u1", profile_id="p1", host_pattern="*.example.com",
        permission="geolocation", state="deny",
    )
    assert await store.check_site_permission(
        user_id="u1", profile_id="p1", host="sub.example.com",
        permission="geolocation",
    ) == "deny"
    # Non-matching host still gets global wildcard
    assert await store.check_site_permission(
        user_id="u1", profile_id="p1", host="other.com",
        permission="geolocation",
    ) == "allow"


@pytest.mark.asyncio
async def test_multi_user_isolation(store):
    await store.set_site_permission(
        user_id="user-a", profile_id="p1",
        host_pattern="a.com", permission="camera", state="allow",
    )
    rows_b = await store.list_site_permissions(user_id="user-b", profile_id="p1")
    assert rows_b == []


@pytest.mark.asyncio
async def test_multi_profile_isolation(store):
    await store.set_site_permission(
        user_id="u1", profile_id="profile-a",
        host_pattern="a.com", permission="microphone", state="allow",
    )
    rows_b = await store.list_site_permissions(user_id="u1", profile_id="profile-b")
    assert rows_b == []
