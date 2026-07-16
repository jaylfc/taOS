"""Regression: the canvas + task stores must boot on an existing DB that
predates the element_id column.

The element_id index used to live in each store's SCHEMA, but BaseStore runs
SCHEMA (executescript) before _post_init. On a real pre-element_id database the
`CREATE INDEX ... (project_id, element_id)` crashed with
`no such column: element_id` before the migration could add the column, which
bricked controller boot after an upgrade (seen live on the Pi). The index is
now created in _post_init after the ALTER, so a legacy DB migrates cleanly.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tinyagentos.projects.canvas.store import ProjectCanvasStore
from tinyagentos.projects.task_store import ProjectTaskStore


def _seed_pre_element_id_db(db: Path) -> None:
    """Create the canvas + task tables WITHOUT the element_id column, as an
    install created before the projects-elements work would have them."""
    c = sqlite3.connect(str(db))
    c.executescript(
        """
        CREATE TABLE project_canvas_elements (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT,
            author_kind TEXT, author_id TEXT, x REAL, y REAL, w REAL, h REAL,
            rotation REAL DEFAULT 0, z_index INTEGER DEFAULT 0, payload TEXT,
            created_at REAL, updated_at REAL, deleted_at REAL);
        CREATE TABLE project_tasks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_task_id TEXT,
            title TEXT, body TEXT DEFAULT '', status TEXT DEFAULT 'open',
            priority INTEGER DEFAULT 0, labels TEXT DEFAULT '[]', assignee_id TEXT,
            created_by TEXT, created_at REAL, updated_at REAL);
        """
    )
    c.commit()
    c.close()


@pytest.mark.asyncio
async def test_stores_boot_on_pre_element_id_db(tmp_path):
    db = tmp_path / "projects.db"
    _seed_pre_element_id_db(db)

    # init() must NOT raise "no such column: element_id".
    canvas = ProjectCanvasStore(db)
    await canvas.init()
    tasks = ProjectTaskStore(db)
    await tasks.init()
    try:
        c = sqlite3.connect(str(db))
        indexes = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        canvas_cols = {r[1] for r in c.execute(
            "PRAGMA table_info(project_canvas_elements)")}
        task_cols = {r[1] for r in c.execute(
            "PRAGMA table_info(project_tasks)")}
        c.close()

        assert "element_id" in canvas_cols
        assert "element_id" in task_cols
        assert "idx_canvas_element" in indexes
        assert "idx_tasks_element" in indexes
    finally:
        await canvas.close()
        await tasks.close()
