from __future__ import annotations

from datetime import datetime, timezone

_KNOWN_SITE_PERMISSIONS = {
    "notifications", "clipboard-read", "clipboard-write",
    "geolocation", "camera", "microphone",
}


class SitePermissionsMixin:

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
