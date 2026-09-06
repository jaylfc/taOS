"""SQLCipher-encrypted cookie store for BrowserApp v2."""
from __future__ import annotations

from pathlib import Path


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
