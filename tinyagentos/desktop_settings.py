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

# Legacy global row id. Existing rows under this id are the defaults for new
# people (migration-free: the old schema/data stays in place). Real auth user
# ids are token_urlsafe values and never equal this string.
DEFAULTS_SCOPE = "user"


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
        """Effective read: code defaults < defaults row < user row.

        When *user_id* is the defaults scope, return only the defaults row
        merged over code defaults (no extra fallback layer).
        """
        base = dict(default)
        defaults_row = await self._get_raw(DEFAULTS_SCOPE, key)
        if defaults_row:
            base.update(defaults_row)
        if user_id == DEFAULTS_SCOPE:
            return base
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
        # never wipes other saved fields and a personal write never freezes the
        # defaults row into the personal row (reads fall back dynamically).
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
        """Get a namespaced preference blob with fallback to defaults.

        Returns an empty dict when nothing has been saved for this namespace
        in either the personal or defaults scope; callers layer their own
        defaults on top.
        """
        safe_key = f"pref:{namespace}"
        return await self._get(user_id, safe_key, {})

    async def save_preference(self, user_id: str, namespace: str, value: dict) -> None:
        """Replace the full preference blob for a namespace in this scope."""
        safe_key = f"pref:{namespace}"
        await self._set(user_id, safe_key, value)
