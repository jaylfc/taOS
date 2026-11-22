"""Migration tests for desktop_settings.

Per-person desktop preferences (settings, dock, windows, widgets and the
per-person "pref:*" namespaces) move to the owner at migration. System-global
settings (setup, auto-update, GPG, taOS agent) stay under the legacy "user"
scope and must survive the migration unchanged.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.desktop_settings import (
    DesktopSettingsStore,
    _LEGACY_GLOBAL_SCOPE,
)

_SYSTEM_NAMESPACES = ("setup", "auto-update", "gpg", "taos_agent", "offline_model")


@pytest_asyncio.fixture
async def store(tmp_path):
    s = DesktopSettingsStore(tmp_path / "desktop.db")
    await s.init()
    yield s
    await s.close()


async def _insert_raw(store, user_id: str, key: str, value: str) -> None:
    assert store._db is not None
    await store._db.execute(
        "INSERT INTO desktop_settings (user_id, key, value) VALUES (?, ?, ?)",
        (user_id, key, value),
    )
    await store._db.commit()


@pytest.mark.asyncio
async def test_migration_moves_only_desktop_prefs_to_owner(store):
    """Pre-PR schema: every row lives under the legacy global "user" scope.

    Migration must move only desktop-preference namespaces to the owner and
    leave system-global settings (setup, auto-update, GPG, taOS agent) at the
    "user" scope intact.
    """
    owner_uid = "owner-42"

    # System-global settings under the legacy "user" scope.
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "pref:setup", '{"dismissed": true}')
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "pref:auto-update",
                      '{"tracked_branch": "dev", "check_enabled": true}')
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "pref:gpg",
                      '{"enabled": true, "required": true, "key_fingerprint": "fp"}')
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "pref:taos_agent",
                      '{"model": "gpt-4", "permitted_models": ["gpt-4"]}')
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "pref:offline_model",
                      '{"model_id": "llama-3", "enabled": true}')

    # Desktop preferences under the legacy "user" scope (should move to owner).
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "settings",
                      '{"wallpaper": "aurora", "mode": "light"}')
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "dock",
                      '{"pinned": ["messages"], "position": "left"}')

    migrated = await store.migrate_legacy_global_to_owner(owner_uid)
    assert migrated is True

    # System-global settings unchanged at the "user" scope.
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "setup") == {"dismissed": True}
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "auto-update") == {
        "tracked_branch": "dev",
        "check_enabled": True,
    }
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "gpg") == {
        "enabled": True,
        "required": True,
        "key_fingerprint": "fp",
    }
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "taos_agent") == {
        "model": "gpt-4",
        "permitted_models": ["gpt-4"],
    }
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "offline_model") == {
        "model_id": "llama-3",
        "enabled": True,
    }

    # Desktop preferences moved to the owner.
    owner_settings = await store.get_settings(owner_uid)
    assert owner_settings["wallpaper"] == "aurora"
    assert owner_settings["mode"] == "light"
    assert owner_settings["dock"]["pinned"] == ["messages"]
    assert owner_settings["dock"]["position"] == "left"

    # Legacy "user" scope no longer serves desktop prefs (code defaults only).
    assert (await store.get_settings(_LEGACY_GLOBAL_SCOPE))["wallpaper"] == "graphite"
    assert (await store.get_settings(_LEGACY_GLOBAL_SCOPE))["mode"] == "dark"

    # Idempotent re-run: nothing left to migrate.
    migrated_again = await store.migrate_legacy_global_to_owner(owner_uid)
    assert migrated_again is False
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "setup") == {"dismissed": True}
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "taos_agent") == {
        "model": "gpt-4",
        "permitted_models": ["gpt-4"],
    }
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "offline_model") == {
        "model_id": "llama-3",
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_migration_noop_when_no_legacy_desktop_rows(store):
    """Fresh install or already-migrated db: migration is a no-op."""
    owner_uid = "owner-42"
    # Only system-global rows at "user" scope; nothing desktop to move.
    await _insert_raw(store, _LEGACY_GLOBAL_SCOPE, "pref:setup", '{"dismissed": true}')
    migrated = await store.migrate_legacy_global_to_owner(owner_uid)
    assert migrated is False
    assert await store.get_preference(_LEGACY_GLOBAL_SCOPE, "setup") == {"dismissed": True}
