"""BrowserApp v2 stores.

- BrowserStore  — regular SQLite, holds profiles/history/bookmarks/caps/push/windows
- BrowserCookieStore — SQLCipher-encrypted, holds cookies; per-user key

Both stores key every row on user_id for OS-grade multi-user isolation.
The query helpers refuse to operate without a user_id argument.
"""
from __future__ import annotations

from pathlib import Path

from tinyagentos.base_store import BaseStore
from tinyagentos.routes.desktop_browser.schema import BROWSER_SCHEMA


class BrowserStore(BaseStore):
    """Regular SQLite store: profiles, history, bookmarks, capabilities,
    push subscriptions, persisted browser-window state.

    Every accessor takes a user_id and refuses to operate without one.
    """
    SCHEMA = BROWSER_SCHEMA

    # Profile helpers (just enough for the multi-user tenancy tests in
    # Task 8 — the rest of the CRUD lands in PR 3 alongside profile.py).

    async def add_profile(
        self,
        *,
        user_id: str,
        profile_id: str,
        name: str,
        color: str | None = None,
        created_at: int,
    ) -> bool:
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "INSERT OR IGNORE INTO profiles (user_id, profile_id, name, color, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, profile_id, name, color, created_at),
        )
        await self._db.commit()
        return cursor.rowcount > 0  # False = slug already taken

    async def list_profiles(self, *, user_id: str) -> list[dict]:
        if not user_id:
            raise ValueError("user_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT profile_id, name, color, created_at "
            "FROM profiles WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "profile_id": r[0],
                "name": r[1],
                "color": r[2],
                "created_at": r[3],
            }
            for r in rows
        ]

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

    async def update_profile(
        self,
        *,
        user_id: str,
        profile_id: str,
        name: str | None = None,
        color: str | None = None,
    ) -> bool:
        """Patch an existing profile's name/color. Returns True if a row was updated."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if name is None and color is None:
            return False
        assert self._db is not None
        # Build dynamic SET clause
        sets: list[str] = []
        params: list[object] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if color is not None:
            sets.append("color = ?")
            params.append(color)
        params.extend([user_id, profile_id])
        cursor = await self._db.execute(
            f"UPDATE profiles SET {', '.join(sets)} "
            f"WHERE user_id = ? AND profile_id = ?",
            params,
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def claim_profile_init(self, *, user_id: str) -> bool:
        """Try to claim the init marker; return True iff this caller won the race.

        profile_init has PRIMARY KEY (user_id), so INSERT OR IGNORE is atomic:
        exactly one concurrent caller gets rowcount == 1 and proceeds to seed
        defaults; all others get rowcount == 0 and skip.
        """
        if not user_id:
            raise ValueError("user_id is required")
        import time as _time
        assert self._db is not None
        cursor = await self._db.execute(
            "INSERT OR IGNORE INTO profile_init (user_id, initialized_at) VALUES (?, ?)",
            (user_id, int(_time.time())),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_profile(self, *, user_id: str, profile_id: str) -> bool:
        """Atomically delete the profile if it is not the user's last.

        Returns True iff the profile was actually deleted.
        Returns False if the profile doesn't exist OR is the last one for the user.

        The COUNT subquery and DELETE execute as a single SQL statement, so two
        concurrent deletes cannot both pass the last-profile guard.
        """
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            """
            DELETE FROM profiles
            WHERE user_id = ? AND profile_id = ?
              AND (SELECT COUNT(*) FROM profiles WHERE user_id = ?) > 1
            """,
            (user_id, profile_id, user_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def add_history(
        self,
        *,
        user_id: str,
        profile_id: str,
        url: str,
        title: str | None,
        visited_at: int,
    ) -> None:
        """Append a history entry. Schema is bag-of-visits — duplicates allowed."""
        if not user_id or not profile_id:
            raise ValueError("user_id and profile_id required")
        assert self._db is not None
        await self._db.execute(
            "INSERT INTO history (user_id, profile_id, url, title, visited_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, profile_id, url, title, visited_at),
        )
        await self._db.commit()

    async def search_history(
        self,
        *,
        user_id: str,
        profile_id: str,
        query: str,
        limit: int = 8,
    ) -> list[dict]:
        """Substring match on url + title for the (user, profile). Most-recent first."""
        if not user_id or not profile_id:
            raise ValueError("user_id and profile_id required")
        assert self._db is not None
        like = f"%{query}%"
        cursor = await self._db.execute(
            "SELECT url, title, visited_at "
            "FROM history "
            "WHERE user_id = ? AND profile_id = ? "
            "  AND (url LIKE ? OR title LIKE ?) "
            "ORDER BY visited_at DESC "
            "LIMIT ?",
            (user_id, profile_id, like, like, limit),
        )
        rows = await cursor.fetchall()
        return [{"url": r[0], "title": r[1], "visited_at": r[2]} for r in rows]

    async def add_bookmark(
        self,
        *,
        user_id: str,
        profile_id: str,
        bookmark_id: str,
        url: str,
        title: str,
        folder_path: str = "/",
        created_at: int,
    ) -> None:
        """Add a bookmark. Idempotent on (user, profile, bookmark_id)."""
        if not user_id or not profile_id or not bookmark_id:
            raise ValueError("user_id, profile_id, bookmark_id required")
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO bookmarks "
            "(user_id, profile_id, bookmark_id, folder_path, url, title, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, profile_id, bookmark_id, folder_path, url, title, created_at),
        )
        await self._db.commit()

    async def list_bookmarks(
        self,
        *,
        user_id: str,
        profile_id: str,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return bookmarks for (user, profile), optionally substring-filtered."""
        if not user_id or not profile_id:
            raise ValueError("user_id and profile_id required")
        assert self._db is not None
        if query:
            like = f"%{query}%"
            cursor = await self._db.execute(
                "SELECT bookmark_id, folder_path, url, title, created_at "
                "FROM bookmarks "
                "WHERE user_id = ? AND profile_id = ? "
                "  AND (url LIKE ? OR title LIKE ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, profile_id, like, like, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT bookmark_id, folder_path, url, title, created_at "
                "FROM bookmarks "
                "WHERE user_id = ? AND profile_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, profile_id, limit),
            )
        rows = await cursor.fetchall()
        return [
            {
                "bookmark_id": r[0],
                "folder_path": r[1],
                "url": r[2],
                "title": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]


class BrowserCookieStore:
    """SQLCipher-encrypted cookie store. Per-user 256-bit key.

    Distinct from BaseStore because aiosqlite can't drive sqlcipher3
    natively. We use the sync sqlcipher3 driver inside an asyncio
    executor — cookie operations are infrequent enough that the executor
    cost is acceptable, and SQLCipher's GIL release on I/O keeps it
    concurrent-friendly in practice.
    """

    def __init__(self, db_path: Path, *, key_hex: str):
        if len(key_hex) != 64:
            raise ValueError("key_hex must be 64 hex chars (256-bit key)")
        try:
            bytes.fromhex(key_hex)
        except ValueError as e:
            raise ValueError("key_hex must contain only hex characters") from e
        self.db_path = db_path
        self._key_hex = key_hex
        self._initialised = False

    async def init(self) -> None:
        import asyncio
        from tinyagentos.routes.desktop_browser.schema import COOKIE_SCHEMA

        def _setup() -> None:
            from sqlcipher3 import dbapi2 as sqlcipher

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlcipher.connect(str(self.db_path))
            try:
                # SQLCipher key — hex form requires the x'…' wrapper
                conn.execute(f"PRAGMA key = \"x'{self._key_hex}'\";")
                conn.executescript(COOKIE_SCHEMA)
                conn.commit()
            finally:
                conn.close()

        await asyncio.get_running_loop().run_in_executor(None, _setup)
        self._initialised = True

    async def close(self) -> None:
        # Each operation opens + closes its own connection; nothing persistent.
        self._initialised = False

    def _connect(self):
        from sqlcipher3 import dbapi2 as sqlcipher

        conn = sqlcipher.connect(str(self.db_path))
        conn.execute(f"PRAGMA key = \"x'{self._key_hex}'\";")
        return conn

    async def set_cookie(
        self,
        *,
        user_id: str,
        profile_id: str,
        host: str,
        path: str,
        name: str,
        value: str,
        expires_at: int | None,
        http_only: bool,
        secure: bool,
        same_site: str | None,
    ) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")

        import asyncio

        def _do() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO cookies "
                    "(user_id, profile_id, host, path, name, value, "
                    " expires_at, http_only, secure, same_site) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id, profile_id, host, path, name, value,
                        expires_at, int(http_only), int(secure), same_site,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.get_running_loop().run_in_executor(None, _do)

    async def get_cookies(
        self,
        *,
        user_id: str,
        profile_id: str,
        host: str,
    ) -> list[dict]:
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")

        import asyncio

        def _do() -> list[dict]:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "SELECT host, path, name, value, expires_at, "
                    "       http_only, secure, same_site "
                    "FROM cookies "
                    "WHERE user_id = ? AND profile_id = ? "
                    "  AND (host = ? OR ? LIKE '%.' || host) "
                    "  AND (expires_at IS NULL OR expires_at > strftime('%s', 'now'))",
                    (user_id, profile_id, host, host),
                )
                rows = cursor.fetchall()
                return [
                    {
                        "host": r[0],
                        "path": r[1],
                        "name": r[2],
                        "value": r[3],
                        "expires_at": r[4],
                        "http_only": bool(r[5]),
                        "secure": bool(r[6]),
                        "same_site": r[7],
                    }
                    for r in rows
                ]
            finally:
                conn.close()

        return await asyncio.get_running_loop().run_in_executor(None, _do)

    async def delete_cookie(
        self,
        *,
        user_id: str,
        profile_id: str,
        host: str,
        path: str,
        name: str,
    ) -> None:
        """Delete a specific cookie by its full primary key."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")

        import asyncio

        def _do() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM cookies "
                    "WHERE user_id = ? AND profile_id = ? "
                    "  AND host = ? AND path = ? AND name = ?",
                    (user_id, profile_id, host, path, name),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.get_running_loop().run_in_executor(None, _do)

    async def delete_profile_cookies(
        self, *, user_id: str, profile_id: str,
    ) -> int:
        """Delete all cookies for a (user, profile). Returns row count."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")

        import asyncio

        def _do() -> int:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM cookies WHERE user_id = ? AND profile_id = ?",
                    (user_id, profile_id),
                )
                conn.commit()
                return cursor.rowcount or 0
            finally:
                conn.close()

        return await asyncio.get_running_loop().run_in_executor(None, _do)
