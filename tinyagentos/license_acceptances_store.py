from __future__ import annotations

"""Store for per-user weights-license acceptances (#169, tsk-virtol).

Some app-catalog services bundle restrictive model weights under a code
license that is otherwise permissive (e.g. musicgen's MIT wrapper around
Meta's CC-BY-NC 4.0 weights). Before such a service installs, the user must
explicitly accept the weights license; this store is the durable record of
that decision, mirroring AppGrantsStore's shape: keyed by
(user_id, app_id, license_id, license_version), last acceptance wins.
There is no revoke -- accepting a license is a point-in-time fact, not a
toggle, and if a manifest's weights_license or license_version ever changes,
that's a new license_id/version pair, so the old acceptance simply no longer
matches and re-acceptance is required.
"""

from datetime import datetime, timezone

import aiosqlite

from tinyagentos.base_store import BaseStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS license_acceptances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    app_id          TEXT NOT NULL,
    license_id      TEXT NOT NULL,
    license_version TEXT NOT NULL DEFAULT '',
    accepted_at     TEXT NOT NULL,
    UNIQUE (user_id, app_id, license_id, license_version)
);
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class LicenseAcceptancesStore(BaseStore):
    """Persistent store for per-user weights-license acceptances."""

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row
        # Migration: add license_version column if missing (for existing installs)
        if self._db is not None:
            cursor = await self._db.execute("PRAGMA table_info(license_acceptances)")
            columns = [row[1] for row in await cursor.fetchall()]
            if "license_version" not in columns:
                await self._db.execute("""
                    ALTER TABLE license_acceptances ADD COLUMN license_version TEXT NOT NULL DEFAULT ''
                """)
            # Recreate unique index to include license_version
            await self._db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_license_acceptances_unique
                ON license_acceptances (user_id, app_id, license_id, license_version)
            """)
            await self._db.commit()

    async def record_acceptance(
        self, user_id: str, app_id: str, license_id: str, license_version: str
    ) -> dict:
        """Record that user_id accepted license_id (with version) for app_id.

        Idempotent via INSERT OR REPLACE: re-accepting the same license+version just
        refreshes accepted_at rather than erroring or duplicating a row.

        license_version must be provided explicitly (empty string if the licence has
        no version). No default is provided to prevent silent mismatches when a
        manifest declares a license_version but the caller omits it.
        """
        if self._db is None:
            raise RuntimeError("LicenseAcceptancesStore not initialised — call init() first")
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO license_acceptances
                (user_id, app_id, license_id, license_version, accepted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, app_id, license_id, license_version, now),
        )
        await self._db.commit()
        row = await (
            await self._db.execute(
                "SELECT * FROM license_acceptances "
                "WHERE user_id = ? AND app_id = ? AND license_id = ? AND license_version = ?",
                (user_id, app_id, license_id, license_version),
            )
        ).fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]

    async def has_accepted(
        self, user_id: str, app_id: str, license_id: str, license_version: str
    ) -> bool:
        """True if user_id has already accepted license_id (with version) for app_id.

        license_version must be provided explicitly (empty string if the licence has
        no version). No default is provided to prevent silent mismatches when a
        manifest declares a license_version but the caller omits it.
        """
        if self._db is None:
            raise RuntimeError("LicenseAcceptancesStore not initialised")
        cursor = await self._db.execute(
            "SELECT 1 FROM license_acceptances "
            "WHERE user_id = ? AND app_id = ? AND license_id = ? AND license_version = ?",
            (user_id, app_id, license_id, license_version),
        )
        return await cursor.fetchone() is not None
