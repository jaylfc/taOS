from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

FOLDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    owner_type TEXT NOT NULL DEFAULT 'global',
    owner_name TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS folder_access (
    folder_id INTEGER NOT NULL,
    agent_name TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT 'read',
    PRIMARY KEY (folder_id, agent_name),
    FOREIGN KEY (folder_id) REFERENCES shared_folders(id) ON DELETE CASCADE
);
"""


class SharedFolderManager(BaseStore):
    SCHEMA = FOLDERS_SCHEMA

    def __init__(self, db_path: Path, storage_dir: Path):
        super().__init__(db_path)
        self.storage_dir = storage_dir

    async def _post_init(self):
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.commit()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_label(label: str) -> str:
        """Reduce a folder name or filename to a single flat, contained label.

        Names and filenames reach the filesystem from API callers, so a "..",
        a "/etc/..." absolute part, or an embedded "../" must never escape the
        shared-folder root (path traversal writing to cron, authorized_keys,
        etc.). A shared-folder name and an uploaded filename are both flat
        labels, so stripping to the basename is the correct, lossless guard.
        """
        # Normalize backslashes to forward slashes first: on POSIX, Path only
        # splits on "/", so a Windows-style "..\evil" would otherwise survive
        # as a literal name. Doing it here makes the guard platform-independent.
        safe = Path(str(label).replace("\\", "/")).name
        if not safe or safe in (".", ".."):
            raise ValueError(f"invalid shared-folder name: {label!r}")
        return safe

    async def create_folder(self, name: str, description: str = "",
                            owner_type: str = "global", owner_name: str = "",
                            agents: list[str] | None = None) -> int:
        now = time.time()
        # Sanitize before it reaches both the DB and the filesystem so the two
        # stay consistent and the physical mkdir cannot escape storage_dir.
        name = self._safe_label(name)
        cursor = await self._db.execute(
            "INSERT INTO shared_folders (name, description, owner_type, owner_name, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, description, owner_type, owner_name, now))
        folder_id = cursor.lastrowid
        if agents:
            for agent in agents:
                await self._db.execute(
                    "INSERT INTO folder_access (folder_id, agent_name, permission) VALUES (?, ?, 'readwrite')",
                    (folder_id, agent))
        await self._db.commit()
        # Create physical directory
        (self.storage_dir / name).mkdir(exist_ok=True)
        return folder_id

    async def list_folders(self, agent_name: str | None = None) -> list[dict]:
        if agent_name:
            async with self._db.execute("""
                SELECT f.id, f.name, f.description, f.owner_type, f.owner_name, fa.permission
                FROM shared_folders f
                JOIN folder_access fa ON fa.folder_id = f.id
                WHERE fa.agent_name = ?
                ORDER BY f.name
            """, (agent_name,)) as cursor:
                return [{"id": r[0], "name": r[1], "description": r[2], "owner_type": r[3],
                         "owner_name": r[4], "permission": r[5]} for r in await cursor.fetchall()]
        async with self._db.execute("SELECT id, name, description, owner_type, owner_name FROM shared_folders ORDER BY name") as cursor:
            return [{"id": r[0], "name": r[1], "description": r[2], "owner_type": r[3], "owner_name": r[4]} for r in await cursor.fetchall()]

    async def delete_folder(self, folder_id: int) -> bool:
        async with self._db.execute("SELECT name FROM shared_folders WHERE id = ?", (folder_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        # Re-sanitize the stored name before an rmtree: defends any legacy row
        # that predates the create-time guard.
        folder_path = self.storage_dir / self._safe_label(row[0])
        if folder_path.exists():
            shutil.rmtree(folder_path)
        await self._db.execute("DELETE FROM shared_folders WHERE id = ?", (folder_id,))
        await self._db.commit()
        return True

    def list_files(self, folder_name: str) -> list[dict]:
        folder_path = self.storage_dir / self._safe_label(folder_name)
        if not folder_path.exists():
            return []
        files = []
        for f in sorted(folder_path.iterdir()):
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 4),
                    "modified": f.stat().st_mtime,
                })
        return files

    async def grant_access(self, folder_id: int, agent_name: str, permission: str = "readwrite"):
        await self._db.execute(
            "INSERT OR REPLACE INTO folder_access (folder_id, agent_name, permission) VALUES (?, ?, ?)",
            (folder_id, agent_name, permission))
        await self._db.commit()

    async def revoke_access(self, folder_id: int, agent_name: str):
        await self._db.execute(
            "DELETE FROM folder_access WHERE folder_id = ? AND agent_name = ?",
            (folder_id, agent_name))
        await self._db.commit()
