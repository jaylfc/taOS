"""Tests for LicenseAcceptancesStore — per-user weights-license acceptances (#169, tsk-virtol)."""
import pytest
import aiosqlite

from tinyagentos.license_acceptances_store import LicenseAcceptancesStore


@pytest.mark.asyncio
class TestLicenseAcceptancesStore:
    async def _store(self, tmp_path):
        s = LicenseAcceptancesStore(tmp_path / "license_acceptances.db")
        await s.init()
        return s

    async def test_has_accepted_false_before_any_record(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0", "4.0") is False
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0", "") is False
        finally:
            await store.close()

    async def test_record_acceptance_then_has_accepted_true(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0", "4.0")
            assert row["user_id"] == "u1"
            assert row["app_id"] == "musicgen"
            assert row["license_id"] == "CC-BY-NC 4.0"
            assert row["license_version"] == "4.0"
            assert "accepted_at" in row
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0", "4.0") is True
            # Without version should not match
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0", "") is False
        finally:
            await store.close()

    async def test_record_acceptance_idempotent(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0", "4.0")
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0", "4.0")
            # Re-accepting doesn't raise and the acceptance still holds
            # (INSERT OR REPLACE keeps a single row per unique key, not a
            # growing duplicate history).
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0", "4.0") is True
        finally:
            await store.close()

    async def test_acceptance_scoped_by_user(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0", "4.0")
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0", "4.0") is True
            assert await store.has_accepted("u2", "musicgen", "CC-BY-NC 4.0", "4.0") is False
        finally:
            await store.close()

    async def test_acceptance_scoped_by_app(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0", "4.0")
            assert await store.has_accepted("u1", "musicgpt", "CC-BY-NC 4.0", "4.0") is False
        finally:
            await store.close()

    async def test_acceptance_scoped_by_license_version(self, tmp_path):
        """A changed license_version requires re-acceptance."""
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "flux-fill", "flux-1-dev-non-commercial-license", "1.0")
            assert await store.has_accepted("u1", "flux-fill", "flux-1-dev-non-commercial-license", "2.0") is False
        finally:
            await store.close()

    async def test_acceptance_scoped_by_license_id(self, tmp_path):
        """A changed weights_license (new license_id) requires re-acceptance."""
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "flux-fill", "flux-1-dev-non-commercial-license", "1.0")
            assert await store.has_accepted("u1", "flux-fill", "flux-1-dev-non-commercial-license-v2", "1.0") is False
        finally:
            await store.close()

    async def test_record_acceptance_uninitialised_raises(self, tmp_path):
        store = LicenseAcceptancesStore(tmp_path / "license_acceptances.db")
        with pytest.raises(RuntimeError):
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0", "4.0")

    async def test_has_accepted_uninitialised_raises(self, tmp_path):
        store = LicenseAcceptancesStore(tmp_path / "license_acceptances.db")
        with pytest.raises(RuntimeError):
            await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0", "4.0")

    async def test_upgrade_from_old_schema_preserves_history_and_allows_new_versions(self, tmp_path):
        """Migration from old schema (UNIQUE without license_version) preserves
        existing acceptances and allows new license_version rows to be added.
        """
        db_path = tmp_path / "license_acceptances.db"
        # Create old schema with UNIQUE(user_id, app_id, license_id) - no license_version in constraint
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE license_acceptances (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         TEXT NOT NULL,
                    app_id          TEXT NOT NULL,
                    license_id      TEXT NOT NULL,
                    license_version TEXT NOT NULL DEFAULT '',
                    accepted_at     TEXT NOT NULL,
                    UNIQUE (user_id, app_id, license_id)
                )
            """)
            await db.commit()
            # Insert an acceptance with version 1.0
            await db.execute("""
                INSERT INTO license_acceptances (user_id, app_id, license_id, license_version, accepted_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("u1", "app1", "license1", "1.0", "2024-01-01T00:00:00"))
            await db.commit()

        # Run migration via init()
        store = LicenseAcceptancesStore(db_path)
        await store.init()

        # Old acceptance should still be recognised
        assert await store.has_accepted("u1", "app1", "license1", "1.0") is True

        # Adding a new version for the same license should succeed (history preserved)
        row = await store.record_acceptance("u1", "app1", "license1", "2.0")
        assert row["license_version"] == "2.0"

        # Both versions should now be accepted
        assert await store.has_accepted("u1", "app1", "license1", "1.0") is True
        assert await store.has_accepted("u1", "app1", "license1", "2.0") is True

        # Verify both rows exist in DB
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT license_version, accepted_at FROM license_acceptances ORDER BY id")
            rows = await cursor.fetchall()
            assert len(rows) == 2
            assert rows[0]["license_version"] == "1.0"
            assert rows[1]["license_version"] == "2.0"

        await store.close()
