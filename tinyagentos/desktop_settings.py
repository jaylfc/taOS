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
        """One-time idempotent migration: move legacy global "user" rows to owner's user_id.

        Returns True if migration ran (rows were moved), False if already migrated
        (no legacy global rows found or owner already has data).

        After migration, the legacy global scope is no longer read for anyone.
        """
        assert self._db is not None

        # Check if migration already happened: owner has any data, or no legacy rows exist
        cursor = await self._db.execute(
            "SELECT 1 FROM desktop_settings WHERE user_id = ? LIMIT 1",
            (owner_user_id,),
        )
        if await cursor.fetchone():
            return False  # Owner already has data, migration not needed

        cursor = await self._db.execute(
            "SELECT 1 FROM desktop_settings WHERE user_id = ? LIMIT 1",
            (_LEGACY_GLOBAL_SCOPE,),
        )
        if not await cursor.fetchone():
            return False  # No legacy global rows, nothing to migrate

        # Move all legacy global rows to owner's user_id
        # Use INSERT OR REPLACE to handle any conflicts (owner might have some keys)
        await self._db.execute(
            """
            INSERT INTO desktop_settings (user_id, key, value)
            SELECT ?, key, value FROM desktop_settings WHERE user_id = ?
            ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
            """,
            (owner_user_id, _LEGACY_GLOBAL_SCOPE),
        )
        # Delete the legacy global rows
        await self._db.execute(
            "DELETE FROM desktop_settings WHERE user_id = ?",
            (_LEGACY_GLOBAL_SCOPE,),
        )
        await self._db.commit()
        return True