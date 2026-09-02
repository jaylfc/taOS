"""Git helpers for agent state versioning inside containers.

All container interactions go through ``exec_in_container`` and
``push_file`` so the same helpers work for both LXC and Docker backends.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import List

from tinyagentos.containers import exec_in_container, push_file

logger = logging.getLogger(__name__)

_REPO_PATH = "/root"

_GITIGNORE_CONTENTS = """\
.env
*.cred
*token*
*.pem
*.p12
*.key
*.secret
.ssh/
caches/
venv/
node_modules/
.browser_profiles/
__pycache__/
*.pyc
.taos/trace/
"""


async def _git(container: str, args: List[str], timeout: int = 60) -> tuple[int, str]:
    rc, out = await exec_in_container(
        container, ["git", "-C", _REPO_PATH, *args], timeout=timeout
    )
    return rc, out


async def git_init(container: str) -> None:
    rc, out = await _git(container, ["init", "-b", "main"])
    if rc != 0:
        raise RuntimeError(f"git init failed: {out}")


async def write_gitignore(container: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".gitignore", delete=False) as tf:
        tf.write(_GITIGNORE_CONTENTS)
        tmp = tf.name
    try:
        rc, out = await push_file(container, tmp, "/root/.gitignore")
    finally:
        os.unlink(tmp)
    if rc != 0:
        raise RuntimeError(f"write .gitignore failed: {out}")


async def git_config_user(container: str, name: str, email: str) -> None:
    await _git(container, ["config", "user.name", name])
    await _git(container, ["config", "user.email", email])


async def git_add_commit(container: str, message: str) -> None:
    rc, out = await _git(container, ["add", "-A"])
    if rc != 0:
        raise RuntimeError(f"git add failed: {out}")
    rc, out = await _git(container, ["commit", "-m", message, "--allow-empty"])
    if rc != 0:
        raise RuntimeError(f"git commit failed: {out}")


async def git_is_dirty(container: str) -> bool:
    rc, out = await _git(container, ["status", "--porcelain"])
    return rc == 0 and bool(out.strip())


async def git_rev_parse(container: str, sha: str) -> str:
    rc, out = await _git(container, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
    if rc != 0:
        raise RuntimeError(f"unknown revision {sha}")
    return out.strip()


async def git_merge_base_is_ancestor(container: str, sha: str) -> bool:
    rc, out = await _git(container, ["merge-base", "--is-ancestor", sha, "HEAD"])
    return rc == 0


async def git_log(container: str) -> List[dict]:
    fmt = "%H%x1f%s%x1f%an%x1f%ae%x1f%ai"
    rc, out = await _git(container, ["log", f"--format={fmt}", "--reverse", "-z"])
    if rc != 0:
        raise RuntimeError(f"git log failed: {out}")
    commits: List[dict] = []
    for line in out.strip().splitlines():
        parts = line.split("\x1f", 4)
        if len(parts) == 5:
            commits.append({
                "sha": parts[0],
                "message": parts[1],
                "author_name": parts[2],
                "author_email": parts[3],
                "date": parts[4],
            })
    return commits


async def git_diff(container: str, sha: str) -> str:
    rc, out = await _git(container, ["show", "--format=", "--patch", sha])
    if rc != 0:
        raise RuntimeError(f"git diff failed for {sha}: {out}")
    return out


async def git_revert(container: str, sha: str) -> str:
    head_sha = (await _git(container, ["rev-parse", "HEAD"]))[1].strip()
    if sha == head_sha:
        return "noop"
    await git_rev_parse(container, sha)
    if not await git_merge_base_is_ancestor(container, sha):
        raise RuntimeError(f"{sha} is not an ancestor of HEAD")
    if await git_is_dirty(container):
        raise RuntimeError("dirty_tree: working tree has uncommitted changes")
    rc, out = await _git(container, ["revert", "--no-edit", f"{sha}..HEAD"])
    if rc != 0:
        raise RuntimeError(f"git revert failed for {sha}: {out}")
    return "reverted"
