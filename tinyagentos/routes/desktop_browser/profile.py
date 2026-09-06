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

import re
import time

from tinyagentos.routes.desktop_browser.store import BrowserCookieStore, BrowserStore


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
    The INSERT OR IGNORE on the PRIMARY KEY (user_id) column makes the claim
    atomic: two concurrent first requests both attempt the insert; only one
    succeeds (rowcount == 1) and that caller seeds the defaults.
    """
    if not user_id:
        raise ValueError("user_id is required")

    if not await store.claim_profile_init(user_id=user_id):
        return  # Another caller already seeded defaults.

    now = int(time.time())
    for default in _DEFAULTS:
        await store.add_profile(
            user_id=user_id,
            profile_id=default["profile_id"],
            name=default["name"],
            color=default["color"],
            created_at=now,
        )


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
    """Create a new profile with auto-slugified ID; appends -2/-3 on collision.

    Uses INSERT OR IGNORE as the authoritative collision guard — the
    pre-check via list_profiles is a fast path only. If a concurrent
    request took the slug between list_profiles and add_profile, the
    INSERT returns False and we increment the suffix and retry.
    """
    if not user_id:
        raise ValueError("user_id is required")
    name = name.strip()
    if not name:
        raise ValueError("name is required")

    base_slug = _slugify(name)
    max_attempts = 100
    for _attempt in range(max_attempts):
        # Fast-path: skip slugs that are already visible in the list.
        existing = await store.list_profiles(user_id=user_id)
        have_ids = {p["profile_id"] for p in existing}
        candidate = base_slug
        n = 2
        while candidate in have_ids:
            candidate = f"{base_slug}-{n}"
            n += 1

        now = int(time.time())
        inserted = await store.add_profile(
            user_id=user_id,
            profile_id=candidate,
            name=name,
            color=color,
            created_at=now,
        )
        if inserted:
            return {
                "profile_id": candidate,
                "name": name,
                "color": color,
                "created_at": now,
            }
        # Race: a concurrent request took this slug between list_profiles and
        # add_profile. Loop again — the next list_profiles call will see it.

    raise RuntimeError(
        f"could not allocate slug for name={name!r} after {max_attempts} attempts"
    )


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
    if name is not None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        name = normalized

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

    The last-profile check is performed atomically inside delete_profile via
    a single SQL statement, so two concurrent deletes cannot both pass the
    guard.
    """
    if not user_id:
        raise ValueError("user_id is required")
    if not profile_id:
        raise ValueError("profile_id is required")

    deleted = await browser_store.delete_profile(
        user_id=user_id, profile_id=profile_id,
    )
    if not deleted:
        # Either the profile didn't exist, or it was the last one.
        # Distinguish by checking whether the profile_id still appears in the list.
        profiles = await browser_store.list_profiles(user_id=user_id)
        if any(p["profile_id"] == profile_id for p in profiles):
            raise LastProfileError(
                "cannot delete the last profile — every user needs at least one"
            )
        return False  # didn't exist

    await cookie_store.delete_profile_cookies(
        user_id=user_id, profile_id=profile_id,
    )
    return True
