"""Tests proving BrowserStore enforces (user_id, …) isolation between users."""
from __future__ import annotations

import time

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    from tinyagentos.routes.desktop_browser.store import BrowserStore

    s = BrowserStore(tmp_path / "browser.sqlite3")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestProfileTenancy:
    async def test_user_a_cannot_see_user_b_profiles(self, store):
        now = int(time.time())
        await store.add_profile(
            user_id="user-a", profile_id="personal", name="Personal", created_at=now,
        )
        await store.add_profile(
            user_id="user-b", profile_id="personal", name="Personal", created_at=now,
        )

        a_profiles = await store.list_profiles(user_id="user-a")
        b_profiles = await store.list_profiles(user_id="user-b")

        assert len(a_profiles) == 1
        assert len(b_profiles) == 1
        # Both users have a profile id of "personal" but they are isolated rows
        # in the database — proven by the (user_id, profile_id) primary key.
        assert a_profiles[0]["name"] == "Personal"
        assert b_profiles[0]["name"] == "Personal"

    async def test_list_profiles_returns_empty_for_new_user(self, store):
        # A user who has never written a profile must get an empty list,
        # not an error and not other users' rows. Catches regressions
        # where the WHERE clause silently fails for empty result sets.
        await store.add_profile(
            user_id="someone-else", profile_id="personal",
            name="Other", created_at=0,
        )

        result = await store.list_profiles(user_id="brand-new-user")

        assert result == []

    async def test_add_profile_requires_user_id(self, store):
        with pytest.raises(ValueError, match="user_id"):
            await store.add_profile(
                user_id="", profile_id="personal", name="Personal", created_at=0,
            )

    async def test_add_profile_requires_profile_id(self, store):
        with pytest.raises(ValueError, match="profile_id"):
            await store.add_profile(
                user_id="user-a", profile_id="", name="Personal", created_at=0,
            )

    async def test_list_profiles_requires_user_id(self, store):
        with pytest.raises(ValueError, match="user_id"):
            await store.list_profiles(user_id="")

    async def test_duplicate_profile_silently_ignored(self, store):
        # (user_id, profile_id) is a primary key — a second insert with
        # the same pair is silently ignored (INSERT OR IGNORE) so
        # ensure_default_profiles can be called concurrently without
        # racing on the PRIMARY KEY constraint.
        now = int(time.time())
        await store.add_profile(
            user_id="user-a", profile_id="personal",
            name="Personal", created_at=now,
        )

        # Second insert with the same pair must NOT raise — it's a no-op
        await store.add_profile(
            user_id="user-a", profile_id="personal",
            name="Different Name",  # ignored
            created_at=now + 100,
        )

        # Original row preserved (INSERT OR IGNORE = first wins)
        profiles = await store.list_profiles(user_id="user-a")
        assert len(profiles) == 1
        assert profiles[0]["name"] == "Personal"
