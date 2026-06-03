from __future__ import annotations


class WindowsMixin:

    async def upsert_window(
        self,
        *,
        user_id: str,
        window_id: str,
        profile_id: str,
        active_tab_id: str | None,
        state_json: str,
    ) -> None:
        """Insert-or-update browser window state for (user, window).

        Used by the windows endpoint to persist debounced UI state.
        """
        if not user_id:
            raise ValueError("user_id is required")
        if not window_id:
            raise ValueError("window_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        assert self._db is not None
        import time
        await self._db.execute(
            "INSERT INTO browser_windows "
            "(user_id, window_id, profile_id, active_tab_id, state, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id, window_id) DO UPDATE SET "
            "  profile_id = excluded.profile_id, "
            "  active_tab_id = excluded.active_tab_id, "
            "  state = excluded.state, "
            "  updated_at = excluded.updated_at",
            (user_id, window_id, profile_id, active_tab_id, state_json, int(time.time())),
        )
        await self._db.commit()

    async def list_windows(self, *, user_id: str) -> list[dict]:
        """Return persisted browser windows for a user, ordered by updated_at desc."""
        if not user_id:
            raise ValueError("user_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT window_id, profile_id, active_tab_id, state, updated_at "
            "FROM browser_windows WHERE user_id = ? "
            "ORDER BY updated_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "window_id": r[0],
                "profile_id": r[1],
                "active_tab_id": r[2],
                "state": r[3],
                "updated_at": r[4],
            }
            for r in rows
        ]

    async def delete_window(self, *, user_id: str, window_id: str) -> bool:
        """Remove a persisted browser window. Returns True if a row was deleted."""
        if not user_id:
            raise ValueError("user_id is required")
        if not window_id:
            raise ValueError("window_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM browser_windows WHERE user_id = ? AND window_id = ?",
            (user_id, window_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0
