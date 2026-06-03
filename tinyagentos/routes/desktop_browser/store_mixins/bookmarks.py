from __future__ import annotations


class BookmarksMixin:

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
