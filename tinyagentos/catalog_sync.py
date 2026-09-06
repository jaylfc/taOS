# tinyagentos/catalog_sync.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def sync_catalog(catalog_dir: Path, repo_url: str | None = None) -> dict:
    """Pull latest catalog from git. If not a git repo, skip."""
    git_dir = catalog_dir / ".git"
    if not git_dir.exists():
        if repo_url:
            # Clone
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", repo_url, str(catalog_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return {"success": False, "error": stdout.decode() if stdout else "clone failed"}
            return {"success": True, "action": "cloned"}
        return {"success": False, "error": "Not a git repo and no repo_url provided"}

    # Pull
    proc = await asyncio.create_subprocess_exec(
        "git", "pull", "--ff-only",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(catalog_dir),
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode() if stdout else ""
    if proc.returncode != 0:
        return {"success": False, "error": output}
    return {"success": True, "action": "pulled", "output": output.strip()}
