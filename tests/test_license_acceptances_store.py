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
