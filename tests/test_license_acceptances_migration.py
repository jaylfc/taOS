"""Test for license acceptances migration - guarded migration and upgrade test."""
import pytest
import aiosqlite
import tempfile
from pathlib import Path

from tinyagentos.license_acceptances_store import LicenseAcceptancesStore


@pytest.mark.asyncio
async def test_migration_from_old_schema_without_version():
    """Test migration from old schema (without license_version) to new schema."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "license_acceptances.db"
        
        # Create old schema without license_version
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
            # Insert some old data (without version)
            await db.execute("""
                INSERT INTO license_acceptances (user_id, app_id, license_id, accepted_at)
                VALUES ('user1', 'app1', 'CC-BY-NC 4.0', '2024-01-01T00:00:00Z')
            """)
            await db.commit()
        
        # Now initialize the store - should migrate
        store = LicenseAcceptancesStore(db_path)
        await store.init()
        
        # Check that the column was added
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(license_acceptances)")
            columns = [row[1] for row in await cursor.fetchall()]
            assert "license_version" in columns
            
            # Check that the table has the correct UNIQUE constraint
            cursor = await db.execute("""
                SELECT sql FROM sqlite_master 
                WHERE type='table' AND name='license_acceptances'
            """)
            table_sql = (await cursor.fetchone())[0]
            assert "UNIQUE (user_id, app_id, license_id, license_version)" in table_sql
        
        # Check that old data has license_version = ''
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT license_version FROM license_acceptances WHERE user_id='user1'")
            row = await cursor.fetchone()
            assert row[0] == ''
        
        await store.close()


@pytest.mark.asyncio
async def test_new_version_adds_row_keeps_history():
    """Test that accepting a new license version adds a row (keeps history)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "license_acceptances.db"
        store = LicenseAcceptancesStore(db_path)
        await store.init()
        
        # Accept version 1.0
        await store.record_acceptance("user1", "app1", "CC-BY-NC 4.0", "1.0")
        
        # Accept version 2.0 - should add a new row, not replace
        await store.record_acceptance("user1", "app1", "CC-BY-NC 4.0", "2.0")
        
        # Both versions should exist
        assert await store.has_accepted("user1", "app1", "CC-BY-NC 4.0", "1.0") is True
        assert await store.has_accepted("user1", "app1", "CC-BY-NC 4.0", "2.0") is True
        
        # Check row count
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM license_acceptances WHERE user_id='user1'")
            count = (await cursor.fetchone())[0]
            assert count == 2
        
        await store.close()


@pytest.mark.asyncio
async def test_re_accepting_same_version_updates_timestamp():
    """Test that re-accepting the same version updates timestamp (INSERT OR REPLACE)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "license_acceptances.db"
        store = LicenseAcceptancesStore(db_path)
        await store.init()
        
        # Accept version 1.0
        row1 = await store.record_acceptance("user1", "app1", "CC-BY-NC 4.0", "1.0")
        ts1 = row1["accepted_at"]
        
        # Re-accept version 1.0
        row2 = await store.record_acceptance("user1", "app1", "CC-BY-NC 4.0", "1.0")
        ts2 = row2["accepted_at"]
        
        # Timestamp should be updated
        assert ts2 >= ts1
        
        # Only one row should exist
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM license_acceptances WHERE user_id='user1'")
            count = (await cursor.fetchone())[0]
            assert count == 1
        
        await store.close()
