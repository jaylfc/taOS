"""Tests for the Profile CRUD module + default-profile auto-creation."""
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


@pytest.mark.asyncio
class TestEnsureDefaultProfiles:
    async def test_creates_personal_and_work_for_new_user(self, store):
        from tinyagentos.routes.desktop_browser.profile import ensure_default_profiles

        await ensure_default_profiles(store, user_id="user-a")

        profiles = await store.list_profiles(user_id="user-a")
        names = {p["name"] for p in profiles}
        assert names == {"Personal", "Work"}

    async def test_idempotent_for_existing_user(self, store):
        from tinyagentos.routes.desktop_browser.profile import ensure_default_profiles

        # First call creates the two defaults
        await ensure_default_profiles(store, user_id="user-a")
        # Second call should not error and should not duplicate
        await ensure_default_profiles(store, user_id="user-a")

        profiles = await store.list_profiles(user_id="user-a")
        assert len(profiles) == 2

    async def test_per_user_isolated(self, store):
        from tinyagentos.routes.desktop_browser.profile import ensure_default_profiles

        await ensure_default_profiles(store, user_id="user-a")
        await ensure_default_profiles(store, user_id="user-b")

        a = await store.list_profiles(user_id="user-a")
        b = await store.list_profiles(user_id="user-b")
        assert len(a) == 2
        assert len(b) == 2


@pytest.mark.asyncio
class TestGetProfileOr404:
    async def test_returns_profile_dict_when_present(self, store):
        from tinyagentos.routes.desktop_browser.profile import (
            get_profile_or_404,
            ensure_default_profiles,
        )

        await ensure_default_profiles(store, user_id="user-a")
        profile = await get_profile_or_404(store, user_id="user-a", profile_id="personal")

        assert profile["name"] == "Personal"

    async def test_raises_when_profile_missing(self, store):
        from tinyagentos.routes.desktop_browser.profile import (
            get_profile_or_404,
            ProfileNotFoundError,
        )

        with pytest.raises(ProfileNotFoundError):
            await get_profile_or_404(store, user_id="user-a", profile_id="nonexistent")

    async def test_raises_when_user_missing(self, store):
        from tinyagentos.routes.desktop_browser.profile import (
            get_profile_or_404,
            ensure_default_profiles,
            ProfileNotFoundError,
        )

        await ensure_default_profiles(store, user_id="user-a")

        # user-b has no profiles — even if "personal" exists for user-a,
        # user-b can't see it
        with pytest.raises(ProfileNotFoundError):
            await get_profile_or_404(store, user_id="user-b", profile_id="personal")
