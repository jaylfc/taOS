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

# Columns added after the first release. Existing databases get them through a
# guarded ALTER in _post_init (the baseline-at-latest migration runner would
# skip a retrofit migration). The UNIQUE key is left alone: a new licence
# version is stored under a different license_id ("<name> <version>"), so the
# older acceptance is kept as history and the new version needs its own row.
_ADDED_COLUMNS = (
    ("license_name", "TEXT NOT NULL DEFAULT ''"),
    ("license_version", "TEXT NOT NULL DEFAULT ''"),
    ("license_url", "TEXT NOT NULL DEFAULT ''"),
    ("username", "TEXT NOT NULL DEFAULT ''"),
)


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class LicenseAcceptancesStore(BaseStore):
    """Persistent store for per-user weights-license acceptances."""

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    async def _post_init(self) -> None:
        if self._db is None:  # pragma: no cover - defensive
            return
        cursor = await self._db.execute("PRAGMA table_info(license_acceptances)")
        have = {row[1] for row in await cursor.fetchall()}
        for name, decl in _ADDED_COLUMNS:
            if name not in have:
                await self._db.execute(f"ALTER TABLE license_acceptances ADD COLUMN {name} {decl}")
        await self._db.commit()

    async def record_acceptance(
        self,
        user_id: str,
        app_id: str,
        license_id: str,
        *,
        license_name: str = "",
        license_version: str = "",
        license_url: str = "",
        username: str = "",
    ) -> dict:
        """Record that user_id accepted license_id for app_id.

        Also keeps the licence name, version, link and the person's username,
        next to the accepted_at timestamp, so the record stands on its own.

        Idempotent via INSERT OR REPLACE: re-accepting the same license just
        refreshes accepted_at rather than erroring or duplicating a row.
        """
        if self._db is None:
            raise RuntimeError("LicenseAcceptancesStore not initialised — call init() first")
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO license_acceptances
                (user_id, app_id, license_id, accepted_at,
                 license_name, license_version, license_url, username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, app_id, license_id, now,
             license_name, license_version, license_url, username),
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
