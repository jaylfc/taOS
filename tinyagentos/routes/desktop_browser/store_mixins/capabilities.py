from __future__ import annotations

from datetime import datetime, timezone


class CapabilitiesMixin:

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
