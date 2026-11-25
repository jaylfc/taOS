"""Test for license acceptances migration - check old unique constraint enforcement."""
import pytest
import aiosqlite
import tempfile
from pathlib import Path

from tinyagentos.license_acceptances_store import LicenseAcceptancesStore


@pytest.mark.asyncio
async def test_old_unique_constraint_enforcement():
    """Test if old unique constraint is enforced."""
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
        
        # Try to insert a duplicate with same user/app/license (should fail if old constraint enforced)
        async with aiosqlite.connect(db_path) as db:
            try:
                await db.execute("""
                    INSERT INTO license_acceptances (user_id, app_id, license_id, license_version, accepted_at)
                    VALUES ('user1', 'app1', 'CC-BY-NC 4.0', '', '2024-01-02T00:00:00Z')
                """)
                await db.commit()
                print("Duplicate insert succeeded (old constraint NOT enforced)")
            except aiosqlite.IntegrityError as e:
                print(f"Duplicate insert failed (old constraint enforced): {e}")
        
        await store.close()
