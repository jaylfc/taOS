"""Test for license acceptances migration - check old unique constraint behavior."""
import pytest
import aiosqlite
import tempfile
from pathlib import Path

from tinyagentos.license_acceptances_store import LicenseAcceptancesStore


@pytest.mark.asyncio
async def test_old_unique_constraint_behavior():
    """Test the behavior of old unique constraint after migration."""
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
        
        # Check the data after migration
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT * FROM license_acceptances")
            rows = await cursor.fetchall()
            for row in rows:
                print(f"Row: {row}")
        
        # Try to accept a new version
        await store.record_acceptance("user1", "app1", "CC-BY-NC 4.0", "2.0")
        
        # Check data again
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT * FROM license_acceptances")
            rows = await cursor.fetchall()
            for row in rows:
                print(f"Row after: {row}")
        
        await store.close()
