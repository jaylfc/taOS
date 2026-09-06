from __future__ import annotations

from datetime import datetime, timedelta, timezone


class DriveSessionsMixin:

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
        if idle_timeout_s <= 0:
            raise ValueError("idle_timeout_s must be positive")
        assert self._db is not None
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=idle_timeout_s)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM drive_sessions WHERE last_op_at <= ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount or 0
