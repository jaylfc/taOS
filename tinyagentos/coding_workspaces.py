from __future__ import annotations

import asyncio
import secrets
import shutil
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

CODING_WORKSPACES_SCHEMA = """
CREATE TABLE IF NOT EXISTS coding_workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""


def _new_workspace_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"cws-{suffix}"


class CodingWorkspaceStore(BaseStore):
    SCHEMA = CODING_WORKSPACES_SCHEMA

    def __init__(self, db_path: Path, workspaces_root: Path):
        super().__init__(db_path)
        self.workspaces_root = workspaces_root

    async def _git_init(self, workspace_dir: Path) -> None:
        # git init can transiently fail under parallel CI/test load. Retry once
        # and capture stderr so a real failure is diagnosable instead of an
        # opaque "git init failed". -q suppresses the default-branch hint.
        last_err = ""
        for _ in range(2):
            proc = await asyncio.create_subprocess_exec(
                "git",
                "init",
                "-q",
                cwd=str(workspace_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _out, err = await proc.communicate()
            if proc.returncode == 0:
                return
            last_err = (err or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"git init failed: {last_err or 'unknown error'}")

    async def create(self, name: str) -> dict:
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        for _ in range(8):
            wid = _new_workspace_id()
            async with self._db.execute(
                "SELECT 1 FROM coding_workspaces WHERE id = ?", (wid,)
            ) as cur:
                if await cur.fetchone() is None:
                    break
        else:
            raise RuntimeError("could not allocate workspace id")

        workspace_dir = (self.workspaces_root / wid).resolve()
        root = self.workspaces_root.resolve()
        if not workspace_dir.is_relative_to(root):
            raise RuntimeError("invalid workspace path")
        workspace_dir.mkdir(parents=True, exist_ok=False)
        try:
            await self._git_init(workspace_dir)
            now = int(time.time())
            row = {
                "id": wid,
                "name": name,
                "path": str(workspace_dir),
                "created_at": now,
            }
            await self._db.execute(
                """INSERT INTO coding_workspaces (id, name, path, created_at)
                   VALUES (?, ?, ?, ?)""",
                (row["id"], row["name"], row["path"], row["created_at"]),
            )
            await self._db.commit()
            return row
        except Exception:
            shutil.rmtree(workspace_dir, ignore_errors=True)
            raise

    async def list(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, name, path, created_at FROM coding_workspaces ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "name": r[1], "path": r[2], "created_at": r[3]}
            for r in rows
        ]

    async def get(self, workspace_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, name, path, created_at FROM coding_workspaces WHERE id = ?",
            (workspace_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {"id": row[0], "name": row[1], "path": row[2], "created_at": row[3]}

    async def delete(self, workspace_id: str) -> bool:
        row = await self.get(workspace_id)
        if row is None:
            return False

        workspace_dir = Path(row["path"]).resolve()
        root = self.workspaces_root.resolve()
        if workspace_dir.is_relative_to(root) and workspace_dir != root and workspace_dir.exists():
            shutil.rmtree(workspace_dir)
            if workspace_dir.exists():
                raise RuntimeError("workspace directory still exists after removal")

        await self._db.execute(
            "DELETE FROM coding_workspaces WHERE id = ?", (workspace_id,)
        )
        await self._db.commit()
        return True