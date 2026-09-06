from __future__ import annotations

"""Store for per-user weights-license acceptances (#169).

Some app-catalog services bundle non-commercial model weights under a code
license that is otherwise permissive (e.g. musicgen's MIT wrapper around
Meta's CC-BY-NC 4.0 weights). Before such a service installs, the user must
explicitly accept the weights license; this store is the durable record of
that decision, mirroring AppGrantsStore's shape: keyed by
(user_id, app_id, license_id), last acceptance wins. There is no revoke --
accepting a license is a point-in-time fact, not a toggle, and if a
manifest's weights_license ever changes, that's a new license_id, so the old
acceptance simply no longer matches and re-acceptance is required.
"""

from datetime import datetime, timezone

import aiosqlite

from tinyagentos.base_store import BaseStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS license_acceptances (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    app_id       TEXT NOT NULL,
    license_id   TEXT NOT NULL,
    accepted_at  TEXT NOT NULL,
    UNIQUE (user_id, app_id, license_id)
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

    async def record_acceptance(self, user_id: str, app_id: str, license_id: str) -> dict:
        """Record that user_id accepted license_id for app_id.

        Idempotent via INSERT OR REPLACE: re-accepting the same license just
        refreshes accepted_at rather than erroring or duplicating a row.
        """
        if self._db is None:
            raise RuntimeError("LicenseAcceptancesStore not initialised — call init() first")
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO license_acceptances
                (user_id, app_id, license_id, accepted_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, app_id, license_id, now),
        )
        await self._db.commit()
        row = await (
            await self._db.execute(
                "SELECT * FROM license_acceptances "
                "WHERE user_id = ? AND app_id = ? AND license_id = ?",
                (user_id, app_id, license_id),
            )
        ).fetchone()
        return _row_to_dict(row)  # type: ignore[return-value]

    async def has_accepted(self, user_id: str, app_id: str, license_id: str) -> bool:
        """True if user_id has already accepted license_id for app_id."""
        if self._db is None:
            raise RuntimeError("LicenseAcceptancesStore not initialised")
        cursor = await self._db.execute(
            "SELECT 1 FROM license_acceptances "
            "WHERE user_id = ? AND app_id = ? AND license_id = ?",
            (user_id, app_id, license_id),
        )
        return await cursor.fetchone() is not None
