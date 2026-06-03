"""BrowserApp v2 stores.

- BrowserStore  — regular SQLite, holds profiles/history/bookmarks/caps/push/windows
- BrowserCookieStore — SQLCipher-encrypted, holds cookies; per-user key

Both stores key every row on user_id for OS-grade multi-user isolation.
The query helpers refuse to operate without a user_id argument.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinyagentos.base_store import BaseStore
from tinyagentos.routes.desktop_browser.schema import BROWSER_SCHEMA
# BrowserCookieStore lives in cookie_store.py; re-exported here for back-compat.
from tinyagentos.routes.desktop_browser.cookie_store import BrowserCookieStore  # noqa: F401


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


    async def list_bookmarks_for_profile(
        self,
        *,
        user_id: str,
        profile_id: str,
    ) -> list[dict]:
        """Returns [{bookmark_id, url, title, created_at}, ...] ordered by created_at DESC."""
        if not user_id or not profile_id:
            raise ValueError("user_id and profile_id required")
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT bookmark_id, url, title, created_at "
            "FROM bookmarks "
            "WHERE user_id = ? AND profile_id = ? "
            "ORDER BY created_at DESC",
            (user_id, profile_id),
        )
        rows = await cursor.fetchall()
        return [
            {
                "bookmark_id": r[0],
                "url": r[1],
                "title": r[2],
                "created_at": r[3],
            }
            for r in rows
        ]

    async def create_bookmark(
        self,
        *,
        user_id: str,
        profile_id: str,
        url: str,
        title: str,
    ) -> str:
        """INSERT a bookmark; returns the new bookmark_id."""
        import secrets
        import time
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not url:
            raise ValueError("url is required")
        if not title:
            raise ValueError("title is required")
        assert self._db is not None
        bookmark_id = secrets.token_urlsafe(12)
        created_at = time.time_ns() // 1_000  # microseconds — unique within a request
        await self._db.execute(
            "INSERT INTO bookmarks "
            "(user_id, profile_id, bookmark_id, folder_path, url, title, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, profile_id, bookmark_id, "/", url, title, created_at),
        )
        await self._db.commit()
        return bookmark_id

    async def delete_bookmark(
        self,
        *,
        user_id: str,
        profile_id: str,
        bookmark_id: str,
    ) -> bool:
        """DELETE; returns True if a row was removed."""
        if not user_id or not profile_id or not bookmark_id:
            raise ValueError("user_id, profile_id, bookmark_id required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM bookmarks "
            "WHERE user_id = ? AND profile_id = ? AND bookmark_id = ?",
            (user_id, profile_id, bookmark_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def find_bookmark_by_url(
        self,
        *,
        user_id: str,
        profile_id: str,
        url: str,
    ) -> dict | None:
        """Returns matching bookmark or None."""
        if not user_id or not profile_id or not url:
            raise ValueError("user_id, profile_id, url required")
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT bookmark_id, url, title, created_at "
            "FROM bookmarks "
            "WHERE user_id = ? AND profile_id = ? AND url = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, profile_id, url),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "bookmark_id": row[0],
            "url": row[1],
            "title": row[2],
            "created_at": row[3],
        }

    # ------------------------------------------------------------------
    # Agent pin helpers
    # ------------------------------------------------------------------

    async def add_pin(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
    ) -> bool:
        """INSERT OR IGNORE. Returns True if newly inserted, False if already existed."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not tab_id:
            raise ValueError("tab_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        assert self._db is not None
        pinned_at = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "INSERT OR IGNORE INTO agent_pins "
            "(user_id, profile_id, tab_id, agent_id, pinned_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, profile_id, tab_id, agent_id, pinned_at),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def add_pin_if_under_cap(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
        max_pins: int,
    ) -> str:
        """Atomic INSERT-with-cap. Returns one of:
          - "added"     — pin newly created
          - "duplicate" — pin already existed for this tuple
          - "at_cap"    — tab already at max_pins, cannot add new pin

        The cap check and INSERT happen in one SQL statement so two concurrent
        calls cannot both pass an N=3 check and produce N=5. Tested at
        concurrency to lock the invariant in.
        """
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not tab_id:
            raise ValueError("tab_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        assert self._db is not None
        pinned_at = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """
            INSERT OR IGNORE INTO agent_pins
                (user_id, profile_id, tab_id, agent_id, pinned_at)
            SELECT ?, ?, ?, ?, ?
            WHERE (
                SELECT COUNT(*) FROM agent_pins
                WHERE user_id = ? AND profile_id = ? AND tab_id = ?
            ) < ?
            """,
            (
                user_id, profile_id, tab_id, agent_id, pinned_at,
                user_id, profile_id, tab_id, max_pins,
            ),
        )
        await self._db.commit()
        if cursor.rowcount > 0:
            return "added"
        # rowcount==0 — either duplicate (PK violation, swallowed by IGNORE)
        # or at-cap (WHERE clause failed). Disambiguate with a single SELECT.
        check = await self._db.execute(
            "SELECT 1 FROM agent_pins "
            "WHERE user_id = ? AND profile_id = ? AND tab_id = ? AND agent_id = ?",
            (user_id, profile_id, tab_id, agent_id),
        )
        existing = await check.fetchone()
        return "duplicate" if existing else "at_cap"

    async def list_pins_for_tab(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
    ) -> list[dict]:
        """Returns list of {agent_id, pinned_at} ordered by pinned_at ASC."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT agent_id, pinned_at FROM agent_pins "
            "WHERE user_id = ? AND profile_id = ? AND tab_id = ? "
            "ORDER BY pinned_at ASC",
            (user_id, profile_id, tab_id),
        )
        rows = await cursor.fetchall()
        return [{"agent_id": r[0], "pinned_at": r[1]} for r in rows]

    async def list_pins_for_user(self, *, user_id: str) -> list[dict]:
        """Returns list of {profile_id, tab_id, agent_id, pinned_at}."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT profile_id, tab_id, agent_id, pinned_at FROM agent_pins "
            "WHERE user_id = ? ORDER BY pinned_at ASC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {"profile_id": r[0], "tab_id": r[1], "agent_id": r[2], "pinned_at": r[3]}
            for r in rows
        ]

    async def count_pins_for_tab(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
    ) -> int:
        """Returns the number of pins on the (user, profile, tab) tuple."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM agent_pins "
            "WHERE user_id = ? AND profile_id = ? AND tab_id = ?",
            (user_id, profile_id, tab_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def delete_pin(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
    ) -> bool:
        """DELETE. Returns True if a row was deleted, False if not present."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not tab_id:
            raise ValueError("tab_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM agent_pins "
            "WHERE user_id = ? AND profile_id = ? AND tab_id = ? AND agent_id = ?",
            (user_id, profile_id, tab_id, agent_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Agent capability helpers
    # ------------------------------------------------------------------

    async def add_capability(
        self,
        *,
        user_id: str,
        profile_id: str,
        agent_id: str,
        host_pattern: str,
        permissions: str,
        expires_at: str | None = None,
    ) -> bool:
        """UPSERT (INSERT OR REPLACE on the PK). Returns True on success."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        if not host_pattern:
            raise ValueError("host_pattern is required")
        if not permissions:
            raise ValueError("permissions is required")
        assert self._db is not None
        granted_at = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR REPLACE INTO agent_capabilities "
            "(user_id, profile_id, agent_id, host_pattern, permissions, granted_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, profile_id, agent_id, host_pattern, permissions, granted_at, expires_at),
        )
        await self._db.commit()
        return True

    async def list_capabilities(
        self,
        *,
        user_id: str,
        profile_id: str,
        agent_id: str | None = None,
    ) -> list[dict]:
        """Returns all grants for (user, profile), optionally filtered by agent_id."""
        assert self._db is not None
        if agent_id is not None:
            cursor = await self._db.execute(
                "SELECT agent_id, host_pattern, permissions, granted_at, expires_at "
                "FROM agent_capabilities "
                "WHERE user_id = ? AND profile_id = ? AND agent_id = ?",
                (user_id, profile_id, agent_id),
            )
        else:
            cursor = await self._db.execute(
                "SELECT agent_id, host_pattern, permissions, granted_at, expires_at "
                "FROM agent_capabilities "
                "WHERE user_id = ? AND profile_id = ?",
                (user_id, profile_id),
            )
        rows = await cursor.fetchall()
        return [
            {
                "agent_id": r[0],
                "host_pattern": r[1],
                "permissions": r[2],
                "granted_at": r[3],
                "expires_at": r[4],
            }
            for r in rows
        ]

    async def revoke_capability(
        self,
        *,
        user_id: str,
        profile_id: str,
        agent_id: str,
        host_pattern: str,
    ) -> bool:
        """DELETE matching grant. Returns True if a row was deleted."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        if not host_pattern:
            raise ValueError("host_pattern is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM agent_capabilities "
            "WHERE user_id = ? AND profile_id = ? AND agent_id = ? AND host_pattern = ?",
            (user_id, profile_id, agent_id, host_pattern),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Drive session helpers
    # ------------------------------------------------------------------

    async def start_drive_session(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
    ) -> None:
        """UPSERT a drive session. Resets started_at + last_op_at to now."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not tab_id:
            raise ValueError("tab_id is required")
        if not agent_id:
            raise ValueError("agent_id is required")
        assert self._db is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR REPLACE INTO drive_sessions "
            "(user_id, profile_id, tab_id, agent_id, started_at, last_op_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, profile_id, tab_id, agent_id, now, now),
        )
        await self._db.commit()

    async def bump_drive_session(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
    ) -> bool:
        """UPDATE last_op_at = now. Returns True iff a row was updated."""
        assert self._db is not None
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE drive_sessions SET last_op_at = ? "
            "WHERE user_id = ? AND profile_id = ? AND tab_id = ? AND agent_id = ?",
            (now, user_id, profile_id, tab_id, agent_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def end_drive_session(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
    ) -> bool:
        """DELETE the session. Returns True iff a row was removed."""
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM drive_sessions "
            "WHERE user_id = ? AND profile_id = ? AND tab_id = ? AND agent_id = ?",
            (user_id, profile_id, tab_id, agent_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def is_driving(
        self,
        *,
        user_id: str,
        profile_id: str,
        tab_id: str,
        agent_id: str,
        idle_timeout_s: float = 30.0,
    ) -> bool:
        """True iff a row exists AND (now - last_op_at) < idle_timeout_s."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT last_op_at FROM drive_sessions "
            "WHERE user_id = ? AND profile_id = ? AND tab_id = ? AND agent_id = ?",
            (user_id, profile_id, tab_id, agent_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        last_op = datetime.fromisoformat(row[0])
        elapsed = (datetime.now(timezone.utc) - last_op).total_seconds()
        return elapsed < idle_timeout_s

    async def prune_expired_drive_sessions(
        self,
        *,
        idle_timeout_s: float = 30.0,
    ) -> int:
        """Atomically delete rows idle for >= idle_timeout_s. Returns rowcount."""
        assert self._db is not None
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=idle_timeout_s)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM drive_sessions WHERE last_op_at <= ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # Capability-check wrappers
    # ------------------------------------------------------------------

    async def check_drive_capability(
        self,
        *,
        user_id: str,
        profile_id: str,
        agent_id: str,
        host: str,
    ) -> bool:
        return await self.check_capability(
            user_id=user_id, profile_id=profile_id, agent_id=agent_id,
            host=host, permission="drive",
        )

    async def check_navigate_capability(
        self,
        *,
        user_id: str,
        profile_id: str,
        agent_id: str,
        host: str,
    ) -> bool:
        return await self.check_capability(
            user_id=user_id, profile_id=profile_id, agent_id=agent_id,
            host=host, permission="navigate",
        )

    async def check_see_cookies_capability(
        self,
        *,
        user_id: str,
        profile_id: str,
        agent_id: str,
        host: str,
    ) -> bool:
        return await self.check_capability(
            user_id=user_id, profile_id=profile_id, agent_id=agent_id,
            host=host, permission="see_cookies",
        )

    async def check_capability(
        self,
        *,
        user_id: str,
        profile_id: str,
        agent_id: str,
        host: str,
        permission: str,
    ) -> bool:
        """Returns True if a non-expired grant for (user, profile, agent) matches
        `host` against its host_pattern and contains `permission`.

        Pattern semantics:
          - "*" matches any host
          - "*.example.com" matches "foo.example.com" and "example.com"
          - anything else: exact match
        """
        rows = await self.list_capabilities(
            user_id=user_id, profile_id=profile_id, agent_id=agent_id,
        )
        now = datetime.now(timezone.utc)
        for row in rows:
            # Expiry check
            expires = row["expires_at"]
            if expires is not None:
                try:
                    exp_dt = datetime.fromisoformat(expires)
                    if exp_dt <= now:
                        continue
                except ValueError:
                    continue

            # Pattern match
            pattern = row["host_pattern"]
            if pattern == "*":
                matched = True
            elif pattern.startswith("*."):
                # "*.example.com" matches "example.com" and "foo.example.com"
                domain = pattern[2:]  # e.g. "example.com"
                matched = host == domain or host.endswith("." + domain)
            else:
                matched = host == pattern

            if not matched:
                continue

            # Permission check
            permissions = [p.strip() for p in row["permissions"].split(",")]
            if permission in permissions:
                return True

        return False


    # ------------------------------------------------------------------
    # Site permission helpers
    # ------------------------------------------------------------------

    async def set_site_permission(
        self,
        *,
        user_id: str,
        profile_id: str,
        host_pattern: str,
        permission: str,
        state: str,
    ) -> None:
        """UPSERT a site permission. state must be 'allow' or 'deny'."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not host_pattern:
            raise ValueError("host_pattern is required")
        if not permission:
            raise ValueError("permission is required")
        if permission not in _KNOWN_SITE_PERMISSIONS:
            raise ValueError(f"unknown permission: {permission}")
        if state not in {"allow", "deny"}:
            raise ValueError("state must be 'allow' or 'deny'")
        assert self._db is not None
        granted_at = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR REPLACE INTO site_permissions "
            "(user_id, profile_id, host_pattern, permission, state, granted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, profile_id, host_pattern, permission, state, granted_at),
        )
        await self._db.commit()

    async def list_site_permissions(
        self,
        *,
        user_id: str,
        profile_id: str,
    ) -> list[dict]:
        """Returns list of {host_pattern, permission, state, granted_at}."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT host_pattern, permission, state, granted_at "
            "FROM site_permissions "
            "WHERE user_id = ? AND profile_id = ? "
            "ORDER BY granted_at",
            (user_id, profile_id),
        )
        rows = await cursor.fetchall()
        return [
            {
                "host_pattern": r[0],
                "permission": r[1],
                "state": r[2],
                "granted_at": r[3],
            }
            for r in rows
        ]

    async def remove_site_permission(
        self,
        *,
        user_id: str,
        profile_id: str,
        host_pattern: str,
        permission: str,
    ) -> bool:
        """DELETE a permission grant. Returns True if removed."""
        if not user_id:
            raise ValueError("user_id is required")
        if not profile_id:
            raise ValueError("profile_id is required")
        if not host_pattern:
            raise ValueError("host_pattern is required")
        if not permission:
            raise ValueError("permission is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM site_permissions "
            "WHERE user_id = ? AND profile_id = ? "
            "  AND host_pattern = ? AND permission = ?",
            (user_id, profile_id, host_pattern, permission),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def check_site_permission(
        self,
        *,
        user_id: str,
        profile_id: str,
        host: str,
        permission: str,
    ) -> str | None:
        """Returns 'allow', 'deny', or None (no grant).

        Pattern semantics (same as check_capability):
          - "*"            matches any host
          - "*.example.com" matches "example.com" and "foo.example.com"
          - anything else: exact match

        When multiple patterns match, the most specific wins:
          exact (2) > subdomain wildcard (1) > global wildcard (0)
        """
        rows = await self.list_site_permissions(user_id=user_id, profile_id=profile_id)
        best_score = -1
        best_state: str | None = None
        for row in rows:
            if row["permission"] != permission:
                continue
            pattern = row["host_pattern"]
            score = -1
            if pattern == host:
                score = 2
            elif pattern.startswith("*."):
                domain = pattern[2:]
                if host == domain or host.endswith("." + domain):
                    score = 1
            elif pattern == "*":
                score = 0
            if score > best_score:
                best_score = score
                best_state = row["state"]
        return best_state


    # ------------------------------------------------------------------
    # Push subscription helpers
    # ------------------------------------------------------------------

    async def upsert_push_subscription(
        self,
        user_id: str,
        device_id: str,
        endpoint: str,
        p256dh_key: str,
        auth_key: str,
        user_agent: str | None = None,
    ) -> None:
        """Insert-or-replace a push subscription. Updates last_seen_at to now.

        created_at is set on first insert and NOT touched on conflict.

        Deduplicates by endpoint within the same user: if an existing row for
        this user has the same endpoint but a different device_id (e.g. the
        browser regenerated its device_id), the old row is removed before the
        upsert so the user doesn't accumulate duplicate notification targets.
        """
        if not user_id:
            raise ValueError("user_id is required")
        if not device_id:
            raise ValueError("device_id is required")
        import time
        assert self._db is not None
        now = int(time.time())
        # Remove stale rows for the same (user, endpoint) with a different device_id.
        await self._db.execute(
            "DELETE FROM push_subscriptions "
            "WHERE user_id = ? AND endpoint = ? AND device_id != ?",
            (user_id, endpoint, device_id),
        )
        await self._db.execute(
            "INSERT INTO push_subscriptions "
            "(user_id, device_id, endpoint, p256dh_key, auth_key, user_agent, created_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, device_id) DO UPDATE SET "
            "  endpoint = excluded.endpoint, "
            "  p256dh_key = excluded.p256dh_key, "
            "  auth_key = excluded.auth_key, "
            "  user_agent = excluded.user_agent, "
            "  last_seen_at = excluded.last_seen_at",
            (user_id, device_id, endpoint, p256dh_key, auth_key, user_agent, now, now),
        )
        await self._db.commit()

    async def list_push_subscriptions(self, user_id: str) -> list[dict]:
        """All push subscriptions for a user, ordered by last_seen_at DESC.

        Each dict has keys: device_id, endpoint, p256dh_key, auth_key,
        user_agent, created_at, last_seen_at.
        """
        if not user_id:
            raise ValueError("user_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT device_id, endpoint, p256dh_key, auth_key, user_agent, created_at, last_seen_at "
            "FROM push_subscriptions "
            "WHERE user_id = ? "
            "ORDER BY last_seen_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "device_id": r[0],
                "endpoint": r[1],
                "p256dh_key": r[2],
                "auth_key": r[3],
                "user_agent": r[4],
                "created_at": r[5],
                "last_seen_at": r[6],
            }
            for r in rows
        ]

    async def delete_push_subscription(self, user_id: str, device_id: str) -> bool:
        """Remove a single subscription. Returns True if a row was deleted."""
        if not user_id:
            raise ValueError("user_id is required")
        if not device_id:
            raise ValueError("device_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM push_subscriptions WHERE user_id = ? AND device_id = ?",
            (user_id, device_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_push_subscription_by_endpoint(self, endpoint: str) -> int:
        """Delete every subscription with this exact endpoint, across all users.

        Returns the number of rows deleted. Used by the 410-Gone cleanup path
        in push.send().
        """
        if not endpoint:
            raise ValueError("endpoint is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        )
        await self._db.commit()
        return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # Push mute helpers
    # ------------------------------------------------------------------

    async def set_push_mute(
        self, user_id: str, agent_id: str, kind: str, muted: bool
    ) -> None:
        """Set or clear a per-(agent, kind) mute.

        muted=True inserts (or replaces), muted=False deletes.
        Raises ValueError if kind is not in _PUSH_MUTE_KINDS.
        """
        if kind not in _PUSH_MUTE_KINDS:
            raise ValueError(f"invalid kind: {kind}")
        assert self._db is not None
        if muted:
            import time
            now = int(time.time())
            await self._db.execute(
                "INSERT OR REPLACE INTO push_mutes (user_id, agent_id, kind, muted_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, agent_id, kind, now),
            )
        else:
            await self._db.execute(
                "DELETE FROM push_mutes WHERE user_id = ? AND agent_id = ? AND kind = ?",
                (user_id, agent_id, kind),
            )
        await self._db.commit()

    async def list_push_mutes(self, user_id: str) -> list[dict]:
        """All mutes for the user. Each dict: {agent_id, kind, muted_at}."""
        if not user_id:
            raise ValueError("user_id is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT agent_id, kind, muted_at FROM push_mutes "
            "WHERE user_id = ? ORDER BY muted_at",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [{"agent_id": r[0], "kind": r[1], "muted_at": r[2]} for r in rows]

    async def is_push_muted(
        self, user_id: str, agent_id: str, kind: str
    ) -> bool:
        """Single-row existence check used by send-side gating."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM push_mutes WHERE user_id = ? AND agent_id = ? AND kind = ?",
            (user_id, agent_id, kind),
        )
        row = await cursor.fetchone()
        return row is not None


_KNOWN_SITE_PERMISSIONS = {
    "notifications", "clipboard-read", "clipboard-write",
    "geolocation", "camera", "microphone",
}

_PUSH_MUTE_KINDS = frozenset({"chat", "drive-started", "download-finished"})
