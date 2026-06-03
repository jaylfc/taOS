from __future__ import annotations


class HistoryMixin:

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
        if limit <= 0:
            raise ValueError("limit must be positive")
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
