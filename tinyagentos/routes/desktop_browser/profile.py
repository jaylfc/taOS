"""Profile CRUD + default-profile bootstrap.

Profiles are within-user namespaces ("Personal", "Work", …) that
isolate cookie jars, history, bookmarks, and agent capability grants.
Every taOS user gets the two default profiles on their first visit
to the browser app — `ensure_default_profiles` is idempotent so
calling it on every request is cheap and safe.

The full CRUD surface (rename, delete, custom colour, profile picker
in the chrome) lands in PR 5. PR 3 only needs:

- ensure_default_profiles  — bootstrap
- get_profile_or_404      — used by the proxy endpoint to validate
                            the `profile_id` query param
"""
from __future__ import annotations

import time

from tinyagentos.routes.desktop_browser.store import BrowserStore


# Defaults bootstrapped per user. profile_id is the URL-safe identifier;
# name is the human-facing label. Colour matches the chrome chip in
# PR 4's frontend mockups.
_DEFAULTS = (
    {"profile_id": "personal", "name": "Personal", "color": "#6c8df0"},
    {"profile_id": "work",     "name": "Work",     "color": "#f5b86b"},
)


class ProfileNotFoundError(Exception):
    """Raised when a (user_id, profile_id) lookup fails."""


async def ensure_default_profiles(store: BrowserStore, *, user_id: str) -> None:
    """Idempotent bootstrap: create Personal + Work for the user on first call only.

    Uses a profile_init table to record that defaults were seeded — subsequent
    calls are a no-op even if the user later deletes those default profiles.
    """
    if not user_id:
        raise ValueError("user_id is required")

    assert store._db is not None
    cursor = await store._db.execute(
        "SELECT initialized_at FROM profile_init WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    if row is not None:
        return  # Already bootstrapped; respect any deletions the user made.

    now = int(time.time())
    for default in _DEFAULTS:
        await store.add_profile(
            user_id=user_id,
            profile_id=default["profile_id"],
            name=default["name"],
            color=default["color"],
            created_at=now,
        )
    await store._db.execute(
        "INSERT OR IGNORE INTO profile_init (user_id, initialized_at) VALUES (?, ?)",
        (user_id, now),
    )
    await store._db.commit()


async def get_profile_or_404(
    store: BrowserStore, *, user_id: str, profile_id: str,
) -> dict:
    """Return the profile dict, raise ProfileNotFoundError if missing.

    The lookup is per-user — user A asking for user B's profile_id
    raises just as if the profile did not exist.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not profile_id:
        raise ValueError("profile_id is required")

    profiles = await store.list_profiles(user_id=user_id)
    for p in profiles:
        if p["profile_id"] == profile_id:
            return p

    raise ProfileNotFoundError(
        f"profile {profile_id!r} not found for user {user_id!r}"
    )


import re

from tinyagentos.routes.desktop_browser.store import BrowserCookieStore


def _slugify(name: str) -> str:
    """Convert a profile name to a URL-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "profile"


async def create_profile(
    store: BrowserStore,
    *,
    user_id: str,
    name: str,
    color: str | None = None,
) -> dict:
    """Create a new profile with auto-slugified ID; appends -2/-3 on collision."""
    if not user_id:
        raise ValueError("user_id is required")
    name = name.strip()
    if not name:
        raise ValueError("name is required")

    base_slug = _slugify(name)
    existing = await store.list_profiles(user_id=user_id)
    have_ids = {p["profile_id"] for p in existing}

    candidate = base_slug
    suffix = 2
    while candidate in have_ids:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1

    now = int(time.time())
    await store.add_profile(
        user_id=user_id,
        profile_id=candidate,
        name=name,
        color=color,
        created_at=now,
    )
    return {
        "profile_id": candidate,
        "name": name,
        "color": color,
        "created_at": now,
    }


async def rename_profile(
    store: BrowserStore,
    *,
    user_id: str,
    profile_id: str,
    name: str | None = None,
    color: str | None = None,
) -> dict:
    """Update an existing profile's name/color. Raises ProfileNotFoundError if missing."""
    if not user_id:
        raise ValueError("user_id is required")
    if not profile_id:
        raise ValueError("profile_id is required")

    updated = await store.update_profile(
        user_id=user_id, profile_id=profile_id, name=name, color=color,
    )
    if not updated:
        # Either the row doesn't exist OR no fields were provided
        # — distinguish by re-reading
        existing = await store.list_profiles(user_id=user_id)
        for p in existing:
            if p["profile_id"] == profile_id:
                return p  # No-op update on existing — return current state
        raise ProfileNotFoundError(
            f"profile {profile_id!r} not found for user {user_id!r}"
        )

    # Re-read to return the updated row
    return await get_profile_or_404(
        store, user_id=user_id, profile_id=profile_id,
    )


class LastProfileError(Exception):
    """Raised when attempting to delete the user's last profile."""


async def delete_profile_cascade(
    browser_store: BrowserStore,
    cookie_store: BrowserCookieStore,
    *,
    user_id: str,
    profile_id: str,
) -> bool:
    """Delete a profile + cascade-delete its cookies.

    Refuses to delete the user's last profile (raises LastProfileError).
    Returns True if the profile was deleted, False if it didn't exist.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not profile_id:
        raise ValueError("profile_id is required")

    profiles = await browser_store.list_profiles(user_id=user_id)
    # Check if the profile being deleted actually exists
    profile_exists = any(p["profile_id"] == profile_id for p in profiles)
    if not profile_exists:
        return False
    if len(profiles) <= 1:
        raise LastProfileError(
            "cannot delete the last profile — every user needs at least one"
        )

    deleted = await browser_store.delete_profile(
        user_id=user_id, profile_id=profile_id,
    )
    if deleted:
        await cookie_store.delete_profile_cookies(
            user_id=user_id, profile_id=profile_id,
        )
    return deleted
