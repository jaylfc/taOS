from __future__ import annotations

import pytest

from tinyagentos.device_pair_requests_store import DevicePairRequestsStore, _SAFE_COLS


@pytest.mark.asyncio
class TestListPendingSafeCols:
    """F13: list_pending must never surface columns outside _SAFE_COLS."""

    @staticmethod
    def _allowed_keys() -> set[str]:
        return {c.strip() for c in _SAFE_COLS.split(",")}

    async def test_extra_column_not_returned(self, tmp_path):
        """If the schema has a column outside _SAFE_COLS, list_pending must
        not surface it in the returned dicts.

        Red-half (proves finding is live on current dev): with SELECT * the
        extra column leaks through. After switching to SELECT {_SAFE_COLS}
        the assertion passes.
        """
        db_path = tmp_path / "pair_requests.sqlite3"
        store = DevicePairRequestsStore(db_path)
        await store.init()
        try:
            # Simulate a schema that has an extra column outside _SAFE_COLS
            await store._db.execute(
                "ALTER TABLE device_pair_requests ADD COLUMN secret_token TEXT DEFAULT ''"
            )
            await store._db.commit()

            await store.create(
                platform="ios",
                display_name="Test Device",
                verify_code="123456",
                requester_ip="10.0.0.1",
            )

            results = await store.list_pending()
            assert len(results) == 1
            row = results[0]
            # The extra column must NOT appear in the returned dict
            assert "secret_token" not in row
            # Every key must belong to _SAFE_COLS
            assert set(row.keys()) == self._allowed_keys()
        finally:
            await store.close()
