from __future__ import annotations

_PUSH_MUTE_KINDS = frozenset({"chat", "drive-started", "download-finished"})


class PushMixin:

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
