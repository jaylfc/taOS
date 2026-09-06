from __future__ import annotations


class ProfilesMixin:

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
