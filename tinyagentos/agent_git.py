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

_STATE_LOCK_PATH = "/tmp/agent_state.lock"


class DirtyTreeError(RuntimeError):
    pass


class NotAncestorError(RuntimeError):
    pass


class ContainerUnreachableError(RuntimeError):
    pass

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
.aws/
credentials
*.credentials
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
        if "bad revision" in out.lower():
            raise RuntimeError(f"unknown revision {sha}")
        raise ContainerUnreachableError(out.strip() or "container unreachable")
    return out.strip()


async def git_merge_base_is_ancestor(container: str, sha: str) -> bool:
    rc, out = await _git(container, ["merge-base", "--is-ancestor", sha, "HEAD"])
    return rc == 0


async def git_log(container: str) -> List[dict]:
    fmt = "%H%x1f%an%x1f%ae%x1f%ai%x1f%s"
    rc, out = await _git(container, ["log", f"--format={fmt}", "--reverse", "-z"])
    if rc != 0:
        raise RuntimeError(f"git log failed: {out}")
    commits: List[dict] = []
    for line in out.strip().split("\x00"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\x1f", 4)
        if len(parts) == 5:
            commits.append({
                "sha": parts[0],
                "author_name": parts[1],
                "author_email": parts[2],
                "date": parts[3],
                "message": parts[4],
            })
    return commits


async def git_diff(container: str, sha: str) -> str:
    rc, out = await _git(container, ["show", "--format=", "--patch", sha])
    if rc != 0:
        if "bad revision" in out.lower():
            raise RuntimeError(f"unknown revision {sha}")
        raise ContainerUnreachableError(out.strip() or "container unreachable")
    return out


async def git_revert(container: str, sha: str) -> str:
    head_rc, head_out = await _git(container, ["rev-parse", "HEAD"])
    if head_rc != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {head_out}")
    head_sha = head_out.strip()
    if sha == head_sha:
        return "noop"
    await git_rev_parse(container, sha)
    if not await git_merge_base_is_ancestor(container, sha):
        raise NotAncestorError(f"{sha} is not an ancestor of HEAD")
    script = (
        "dirty=$(git -C /root status --porcelain); "
        'test -z "$dirty" && git -C /root reset --hard ' + sha
    )
    rc, out = await exec_in_container(
        container,
        ["bash", "-c", f"flock {_STATE_LOCK_PATH} -c {script!r}"],
    )
    if rc != 0:
        raise DirtyTreeError("dirty_tree: working tree has uncommitted changes")
    return "reverted"
