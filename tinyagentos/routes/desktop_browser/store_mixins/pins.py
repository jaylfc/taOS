from __future__ import annotations

from datetime import datetime, timezone


class PinsMixin:

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
