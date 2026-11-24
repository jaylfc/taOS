from __future__ import annotations
import json
from pathlib import Path
from tinyagentos.base_store import BaseStore

DEFAULT_SETTINGS = {
    "mode": "dark",
    "accentColor": "#667eea",
    # Must match the frontend's own boot-time default (theme-store's
    # DEFAULT_WP, "graphite"). "default" is a real, selectable wallpaper
    # ("Classic") elsewhere in the catalog, so it cannot double as the
    # "nothing saved yet" sentinel without colliding with that choice.
    "wallpaper": "graphite",
    "dockMagnification": False,
    "topBarOpacity": 0.95,
}

DEFAULT_DOCK = {
    "pinned": ["messages", "agents", "files", "store", "settings"],
    "iconSize": "medium",
    "position": "bottom",
}

# Legacy global row id. After migration this is no longer read for anyone.
# Real auth user ids are token_urlsafe values and never equal this string.
_LEGACY_GLOBAL_SCOPE = "user"

# Keys stored directly under a user scope (no "pref:" prefix) that are
# per-person desktop preferences and must move to the owner at migration.
_DESKTOP_KEYS = ("settings", "dock", "windows", "widgets")

# Namespaced preferences (stored as "pref:<namespace>") that are SYSTEM-GLOBAL:
# read/written by system routes (setup, auto-update, GPG, taOS agent, offline_model)
# and must stay under the legacy "user" scope; the migration must NOT move or delete them.
_SYSTEM_NAMESPACES = ("setup", "auto-update", "gpg", "taos_agent", "offline_model")


class DesktopSettingsStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS desktop_settings (
        user_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (user_id, key)
    );
    """

    async def _get_raw(self, user_id: str, key: str) -> dict | None:
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT value FROM desktop_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        row = await cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    async def _get(self, user_id: str, key: str, default: dict) -> dict:
        """Effective read: code defaults < user row only.

        The legacy global "user" scope is no longer used as a fallback.
        New users get code defaults directly.
        """
        base = dict(default)
        user_row = await self._get_raw(user_id, key)
        if user_row:
            base.update(user_row)
        return base

    async def _get_personal(self, user_id: str, key: str) -> dict:
        """Return only this scope's own stored blob, for read-merge-write."""
        row = await self._get_raw(user_id, key)
        return dict(row) if row else {}

    async def _set(self, user_id: str, key: str, value: dict) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO desktop_settings (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, json.dumps(value)),
        )
        await self._db.commit()

    async def get_settings(self, user_id: str) -> dict:
        settings = await self._get(user_id, "settings", DEFAULT_SETTINGS)
        dock = await self.get_dock(user_id)
        settings["dock"] = dock
        return settings

    async def update_settings(self, user_id: str, updates: dict) -> None:
        # Read-merge-write over this scope's own row so a partial payload
        # never wipes other saved fields.
        current = await self._get_personal(user_id, "settings")
        current.update(updates)
        await self._set(user_id, "settings", current)

    async def get_dock(self, user_id: str) -> dict:
        return await self._get(user_id, "dock", DEFAULT_DOCK)

    async def update_dock(self, user_id: str, updates: dict) -> None:
        current = await self._get_personal(user_id, "dock")
        current.update(updates)
        await self._set(user_id, "dock", current)

    async def get_windows(self, user_id: str) -> list:
        data = await self._get(user_id, "windows", {"positions": []})
        return data.get("positions", [])

    async def save_windows(self, user_id: str, positions: list) -> None:
        # Windows layout is saved as a complete snapshot for this scope.
        await self._set(user_id, "windows", {"positions": positions})

    async def get_widgets(self, user_id: str) -> list:
        data = await self._get(user_id, "widgets", {"widgets": []})
        return data.get("widgets", [])

    async def save_widgets(self, user_id: str, widgets: list) -> None:
        await self._set(user_id, "widgets", {"widgets": widgets})

    async def get_preference(self, user_id: str, namespace: str) -> dict:
        """Get a namespaced preference blob for this user.

        Returns an empty dict when nothing has been saved for this namespace
        by this user; callers layer their own defaults on top.
        """
        safe_key = f"pref:{namespace}"
        return await self._get(user_id, safe_key, {})

    async def save_preference(self, user_id: str, namespace: str, value: dict) -> None:
        """Replace the full preference blob for a namespace in this user's scope."""
        safe_key = f"pref:{namespace}"
        await self._set(user_id, safe_key, value)

    async def migrate_legacy_global_to_owner(self, owner_user_id: str) -> bool:
        """One-time idempotent migration: move legacy global "user" desktop
        preferences to the owner's user_id.

        System-global preferences (setup, auto-update, GPG, taOS agent) remain
        under the legacy "user" scope -- they are read/written there by the
        system routes and are NOT moved or deleted here.

        Returns True if migration ran (desktop rows were moved), False if there
        are no legacy global desktop-preference rows to move (already migrated
        or a fresh install with no legacy data).
        """
        assert self._db is not None

        desktop_placeholders = ",".join("?" for _ in _DESKTOP_KEYS)
        system_placeholders = ",".join("?" for _ in _SYSTEM_NAMESPACES)

        # Are there any legacy global desktop-preference rows to migrate?
        cursor = await self._db.execute(
            f"""
            SELECT 1 FROM desktop_settings
            WHERE user_id = ? AND (
                key IN ({desktop_placeholders})
                OR (key LIKE 'pref:%' AND substr(key, 6) NOT IN ({system_placeholders}))
            )
            LIMIT 1
            """,
            (_LEGACY_GLOBAL_SCOPE, *_DESKTOP_KEYS, *_SYSTEM_NAMESPACES),
        )
        if not await cursor.fetchone():
            return False  # Nothing to migrate

        # Move desktop key rows (settings, dock, windows, widgets) to owner.
        # Move non-system namespaced preferences to owner, then delete the
        # migrated desktop rows from the legacy global scope. All three writes
        # run in one transaction: if the preference copy raises, roll back so
        # no later write commits a half migration and the legacy rows stay
        # untouched. System-global pref:* rows stay at the "user" scope.
        try:
            await self._db.execute(
                f"""
                INSERT INTO desktop_settings (user_id, key, value)
                SELECT ?, key, value FROM desktop_settings
                WHERE user_id = ? AND key IN ({desktop_placeholders})
                ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
                """,
                (owner_user_id, _LEGACY_GLOBAL_SCOPE, *_DESKTOP_KEYS),
            )
            await self._db.execute(
                f"""
                INSERT INTO desktop_settings (user_id, key, value)
                SELECT ?, key, value FROM desktop_settings
                WHERE user_id = ? AND key LIKE 'pref:%'
                  AND substr(key, 6) NOT IN ({system_placeholders})
                ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
                """,
                (owner_user_id, _LEGACY_GLOBAL_SCOPE, *_SYSTEM_NAMESPACES),
            )
            await self._db.execute(
                f"""
                DELETE FROM desktop_settings
                WHERE user_id = ? AND (
                    key IN ({desktop_placeholders})
                    OR (key LIKE 'pref:%' AND substr(key, 6) NOT IN ({system_placeholders}))
                )
                """,
                (_LEGACY_GLOBAL_SCOPE, *_DESKTOP_KEYS, *_SYSTEM_NAMESPACES),
            )
        except Exception:
            await self._db.rollback()
            raise
        await self._db.commit()
        return True
