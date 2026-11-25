"""Tests for LicenseAcceptancesStore — per-user weights-license acceptances (#169)."""
import pytest

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
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0") is False
        finally:
            await store.close()

    async def test_record_acceptance_then_has_accepted_true(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            row = await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0")
            assert row["user_id"] == "u1"
            assert row["app_id"] == "musicgen"
            assert row["license_id"] == "CC-BY-NC 4.0"
            assert "accepted_at" in row
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0") is True
        finally:
            await store.close()

    async def test_record_acceptance_idempotent(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0")
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0")
            # Re-accepting doesn't raise and the acceptance still holds
            # (INSERT OR REPLACE keeps a single row per unique key, not a
            # growing duplicate history).
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0") is True
        finally:
            await store.close()

    async def test_acceptance_scoped_by_user(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0")
            assert await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0") is True
            assert await store.has_accepted("u2", "musicgen", "CC-BY-NC 4.0") is False
        finally:
            await store.close()

    async def test_acceptance_scoped_by_app(self, tmp_path):
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0")
            assert await store.has_accepted("u1", "musicgpt", "CC-BY-NC 4.0") is False
        finally:
            await store.close()

    async def test_acceptance_scoped_by_license_id(self, tmp_path):
        """A changed weights_license (new license_id) requires re-acceptance."""
        store = await self._store(tmp_path)
        try:
            await store.record_acceptance("u1", "flux-fill", "flux-1-dev-non-commercial-license")
            assert await store.has_accepted("u1", "flux-fill", "flux-1-dev-non-commercial-license-v2") is False
        finally:
            await store.close()

    async def test_record_acceptance_uninitialised_raises(self, tmp_path):
        store = LicenseAcceptancesStore(tmp_path / "license_acceptances.db")
        with pytest.raises(RuntimeError):
            await store.record_acceptance("u1", "musicgen", "CC-BY-NC 4.0")

    async def test_has_accepted_uninitialised_raises(self, tmp_path):
        store = LicenseAcceptancesStore(tmp_path / "license_acceptances.db")
        with pytest.raises(RuntimeError):
            await store.has_accepted("u1", "musicgen", "CC-BY-NC 4.0")


@pytest.mark.asyncio
class TestAcceptanceRecordAndUpgrade:
    async def test_record_keeps_name_version_url_person_timestamp(self, tmp_path):
        s = LicenseAcceptancesStore(tmp_path / "la.db")
        await s.init()
        try:
            row = await s.record_acceptance(
                "u1", "app", "Gemma Terms of Use 1.1", license_name="Gemma Terms of Use",
                license_version="1.1", license_url="https://ai.google.dev/gemma/terms",
                username="alice",
            )
            assert row["license_name"] == "Gemma Terms of Use"
            assert row["license_version"] == "1.1"
            assert row["license_url"] == "https://ai.google.dev/gemma/terms"
            assert row["username"] == "alice" and row["user_id"] == "u1"
            assert row["accepted_at"]
        finally:
            await s.close()

    async def test_new_version_needs_new_acceptance_old_one_kept(self, tmp_path):
        s = LicenseAcceptancesStore(tmp_path / "la.db")
        await s.init()
        try:
            await s.record_acceptance("u1", "app", "Lic 1.0", license_name="Lic", license_version="1.0")
            assert await s.has_accepted("u1", "app", "Lic 1.0")
            assert not await s.has_accepted("u1", "app", "Lic 2.0")
            await s.record_acceptance("u1", "app", "Lic 2.0", license_name="Lic", license_version="2.0")
            assert await s.has_accepted("u1", "app", "Lic 1.0")
            assert await s.has_accepted("u1", "app", "Lic 2.0")
        finally:
            await s.close()

    async def test_existing_database_from_old_schema_upgrades_and_keeps_rows(self, tmp_path):
        import sqlite3
        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.executescript(
            "CREATE TABLE license_acceptances (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id TEXT NOT NULL, app_id TEXT NOT NULL, license_id TEXT NOT NULL,"
            " accepted_at TEXT NOT NULL, UNIQUE (user_id, app_id, license_id));"
            "INSERT INTO license_acceptances (user_id, app_id, license_id, accepted_at)"
            " VALUES ('u1', 'musicgen', 'CC-BY-NC 4.0', '2026-01-01T00:00:00+00:00');"
        )
        con.commit()
        con.close()
        s = LicenseAcceptancesStore(path)
        await s.init()
        try:
            assert await s.has_accepted("u1", "musicgen", "CC-BY-NC 4.0")
            row = await s.record_acceptance("u2", "musicgen", "CC-BY-NC 4.0", username="bob",
                                            license_name="CC-BY-NC 4.0")
            assert row["username"] == "bob"
        finally:
            await s.close()
        s2 = LicenseAcceptancesStore(path)
        await s2.init()  # second open must be a no-op on the upgraded schema
        try:
            assert await s2.has_accepted("u1", "musicgen", "CC-BY-NC 4.0")
        finally:
            await s2.close()
