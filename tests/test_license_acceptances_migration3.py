"""Test for license acceptances migration - check old unique constraint blocks new versions."""
import pytest
import aiosqlite
import tempfile
from pathlib import Path

from tinyagentos.license_acceptances_store import LicenseAcceptancesStore


@pytest.mark.asyncio
async def test_old_unique_constraint_blocks_new_version():
    """Test that old unique constraint blocks inserting new version after migration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "license_acceptances.db"
        
        # Create old schema
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE license_acceptances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    license_id TEXT NOT NULL,
                    accepted_at TEXT NOT NULL,
                    UNIQUE (user_id, app_id, license_id)
                )
            """)
            await db.execute("""
                INSERT INTO license_acceptances (user_id, app_id, license_id, accepted_at)
                VALUES ('user1', 'app1', 'CC-BY-NC 4.0', '2024-01-01T00:00:00Z')
            """)
            await db.commit()
        
        # Migrate
        store = LicenseAcceptancesStore(db_path)
        await store.init()
        
        # Try to accept a new version - should fail due to old UNIQUE constraint
        try:
            await store.record_acceptance("user1", "app1", "CC-BY-NC 4.0", "2.0")
            print("SUCCESS: New version accepted")
        except Exception as e:
            print(f"FAILED: {e}")
            raise
        
        await store.close()
