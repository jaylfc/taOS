"""Tests for BrowserStore — schema applies cleanly, all expected tables exist."""
from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    from tinyagentos.routes.desktop_browser.store import BrowserStore

    s = BrowserStore(tmp_path / "browser.sqlite3")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestBrowserStoreSchema:
    async def test_init_creates_db_file(self, store, tmp_path):
        assert (tmp_path / "browser.sqlite3").exists()

    async def test_all_tables_exist(self, store, tmp_path):
        # Use sync sqlite3 to introspect — cheaper than async for a one-shot read
        conn = sqlite3.connect(tmp_path / "browser.sqlite3")
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name"
            ).fetchall()
            tables = {r[0] for r in rows}
        finally:
            conn.close()

        expected = {
            "profiles",
            "history",
            "bookmarks",
            "agent_capabilities",
            "push_subscriptions",
            "browser_windows",
        }
        assert expected <= tables, f"missing: {expected - tables}"

    async def test_idempotent_init(self, tmp_path):
        from tinyagentos.routes.desktop_browser.store import BrowserStore

        s1 = BrowserStore(tmp_path / "browser.sqlite3")
        await s1.init()
        await s1.close()

        # Second init on the same path must not raise
        s2 = BrowserStore(tmp_path / "browser.sqlite3")
        await s2.init()
        await s2.close()
