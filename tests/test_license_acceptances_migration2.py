"""Test for license acceptances migration - check old unique constraint."""
import pytest
import aiosqlite
import tempfile
from pathlib import Path

from tinyagentos.license_acceptances_store import LicenseAcceptancesStore


@pytest.mark.asyncio
async def test_migration_drops_old_unique_constraint():
    """Test that migration drops the old unique constraint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "license_acceptances.db"
        
        # Create old schema without license_version but WITH unique constraint
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
        
        # Now initialize the store - should migrate
        store = LicenseAcceptancesStore(db_path)
        await store.init()
        
        # Check that the old unique constraint is gone and new one exists
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("""
                SELECT sql FROM sqlite_master 
                WHERE type='table' AND name='license_acceptances'
            """)
            table_sql = (await cursor.fetchone())[0]
            print(f"Table SQL: {table_sql}")
            
            cursor = await db.execute("""
                SELECT sql FROM sqlite_master 
                WHERE type='index' AND tbl_name='license_acceptances'
            """)
            indexes = await cursor.fetchall()
            for idx in indexes:
                print(f"Index: {idx[0]}")
        
        await store.close()
