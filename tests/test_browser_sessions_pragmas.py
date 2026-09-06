"""RED test for browser_sessions pragmas.

This test demonstrates that browser_sessions.py is missing WAL mode
and busy_timeout pragmas that all other stores have.
"""
from __future__ import annotations

import aiosqlite
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.browser_sessions import BrowserSessionManager


@pytest.mark.asyncio
async def test_browser_sessions_has_wal_and_busy_timeout(tmp_path: Path) -> None:
    """RED test: browser_sessions must have WAL mode and busy_timeout pragmas.
    
    The current implementation is missing these pragmas that all other
    SQLite stores (otel span store, trace store) have. This test will FAIL
    before the fix is applied.
    """
    db_path = tmp_path / "browser_sessions.db"
    manager = BrowserSessionManager(db_path=db_path, mock=True)
    
    # Initialize the database
    await manager.init()
    
    # Connect to the database directly to check pragmas
    conn = await aiosqlite.connect(str(db_path))
    
    # Check journal_mode is WAL
    cursor = await conn.execute("PRAGMA journal_mode")
    journal_mode = await cursor.fetchone()
    await cursor.close()
    
    # RED TEST EXPECTATION: journal_mode should be 'wal' or 'WAL'
    assert journal_mode[0].upper() == "WAL", f"Expected journal_mode WAL, got {journal_mode[0]}"
    
    # Check busy_timeout is set (default is 5000, so we expect at least 30000)
    cursor = await conn.execute("PRAGMA busy_timeout")
    busy_timeout = await cursor.fetchone()
    await cursor.close()
    
    # RED TEST EXPECTATION: busy_timeout should be 5000 (5 seconds) to match other stores
    assert busy_timeout[0] == 5000, f"Expected busy_timeout 5000, got {busy_timeout[0]}"
    
    # Verify table can be queried without issues
    cursor = await conn.execute("SELECT COUNT(*) FROM browser_sessions")
    count = await cursor.fetchone()
    await cursor.close()
    
    assert count[0] == 0
    
    await conn.close()
    await manager.close()