"""Git helpers for agent state versioning inside containers.

All container interactions go through ``exec_in_container`` and
``push_file`` so the same helpers work for both LXC and Docker backends.

The repo root is the agent's whole home directory and every commit is made
with ``git add -A``, so the versioned scope is an ALLOWLIST (``_STATE_PATHS``)
rather than a list of secret patterns to deny — see ``_build_gitignore``.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Iterable, List, NoReturn

from tinyagentos.containers import exec_in_container, push_file

logger = logging.getLogger(__name__)

_REPO_PATH = "/root"

_STATE_LOCK_PATH = "/tmp/agent_state.lock"

# The first `git add -A` only descends into the allowlisted paths below, so it
# never walks .cache/, .local/ or .venv/ — but an image that ships a populated
# workspace/ can still outlast an ordinary git call on Pi-class storage, and a
# timeout there disables versioning for the whole deployment.
_INITIAL_COMMIT_TIMEOUT = 300


class DirtyTreeError(RuntimeError):
    pass


class NotAncestorError(RuntimeError):
    pass


class ContainerUnreachableError(RuntimeError):
    pass


class GitOperationError(RuntimeError):
    """A git command reached the container and failed on repo state.

    Distinct from ``ContainerUnreachableError``: the container answered, but
    git could not do the work (corrupt index, unwritable .git, missing
    object). Both map to 409 — the class is what tells the two apart in a log.
    """


# Per-framework AGENTS.md path inside the agent's container. Frameworks read
# this file on every turn to pick up agent rules (per the taosmd contract —
# see issue #378). It lives here because the versioned scope below derives
# from it: adding a framework must not also mean remembering to version its
# rules file. ``deployer`` re-exports the name it has always exposed.
AGENTS_MD_PATHS: dict[str, str] = {
    "openclaw": "/root/.openclaw/AGENTS.md",
    "hermes": "/root/.hermes/AGENTS.md",
}


def _home_relative(path: str) -> str:
    """Return *path* relative to the agent home, for a .gitignore entry.

    Deliberately loud at import time rather than skipping the entry: a path
    outside the home cannot be versioned by a repo rooted at the home, and a
    silently dropped one would leave that framework's rules unversioned with
    nothing to notice it.
    """
    prefix = _REPO_PATH.rstrip("/") + "/"
    if not path.startswith(prefix):
        raise ValueError(
            f"{path} is not inside the agent home {_REPO_PATH}, so the agent "
            f"state repo cannot version it — put the file under {_REPO_PATH} "
            f"or drop it from AGENTS_MD_PATHS"
        )
    return path[len(prefix):]


# Everything the agent state repo versions. The repo root IS the agent home,
# so the scope has to be an allowlist: a denylist over a home directory can
# never be complete — every framework install drops another config file
# carrying an API key (.hermes/config.yaml), a bridge token (.openclaw/env)
# or a multi-gigabyte cache tree. A new framework adds a state path here,
# never a new secret pattern.
#
# Deliberately out of scope, and covered by the leading "*": .ssh/, .taos/
# (the trace bind mount and the committer's own log), the framework config
# files that sit next to these AGENTS.md files, shell history, and every
# cache/venv tree.
_STATE_PATHS: tuple[str, ...] = (
    ".gitignore",
    "AGENTS.md",
    "workspace/",
    "memory/",
    *sorted(_home_relative(p) for p in AGENTS_MD_PATHS.values()),
)


def _build_gitignore(state_paths: Iterable[str]) -> str:
    """Render the allowlist .gitignore: ignore everything, re-include state.

    git refuses to re-include a file whose parent directory is excluded, so
    every parent of a re-included file is re-included too. That is also what
    keeps the scan cheap: git descends into the allowlisted directories only,
    and the excluded ones (.cache/, .local/, .venv/) are never walked.
    """
    lines = [
        "# taOS agent state repo — ALLOWLIST: everything under the agent home",
        "# is ignored and only the paths re-included below are versioned.",
        "# Generated from _STATE_PATHS in tinyagentos/agent_git.py — edit there,",
        "# a deploy overwrites this file.",
        "*",
    ]
    for path in state_paths:
        if path.endswith("/"):
            lines.append(f"!/{path}")
            lines.append(f"!/{path}**")
            continue
        parents = path.split("/")[:-1]
        for depth in range(1, len(parents) + 1):
            entry = "!/" + "/".join(parents[:depth]) + "/"
            if entry not in lines:
                lines.append(entry)
        lines.append(f"!/{path}")
    return "\n".join(lines) + "\n"


_GITIGNORE_CONTENTS = _build_gitignore(_STATE_PATHS)

# git words a missing object differently per subcommand and version:
# `rev-parse` says "bad revision", `show` says "ambiguous argument ...:
# unknown revision or path not in the working tree", older builds say "bad
# object". Every one of them is a 404, not an unreachable container.
_UNKNOWN_REV_MARKERS = (
    "bad revision",
    "unknown revision",
    "ambiguous argument",
    "bad object",
)


# git reports a missing object on a line of its own prefixed "fatal:", and only
# those lines are searched for the markers. Matching anywhere in the container's
# combined output would let an unrelated failure that happens to quote one of
# these phrases — an incus "Error: Instance is not running (ambiguous
# argument)" — turn an unreachable container into a 404 "unknown revision".
_GIT_FATAL_PREFIX = "fatal:"


def _raise_unknown_revision_or_unreachable(sha: str, out: str) -> NoReturn:
    for line in out.lower().splitlines():
        line = line.strip()
        if not line.startswith(_GIT_FATAL_PREFIX):
            continue
        if any(marker in line for marker in _UNKNOWN_REV_MARKERS):
            raise RuntimeError(f"unknown revision {sha}")
    raise ContainerUnreachableError(out.strip() or "container unreachable")


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
    rc, out = await _git(container, ["add", "-A"], timeout=_INITIAL_COMMIT_TIMEOUT)
    if rc != 0:
        raise RuntimeError(f"git add failed: {out}")
    rc, out = await _git(
        container,
        ["commit", "-m", message, "--allow-empty"],
        timeout=_INITIAL_COMMIT_TIMEOUT,
    )
    if rc != 0:
        raise RuntimeError(f"git commit failed: {out}")


async def git_is_dirty(container: str) -> bool:
    rc, out = await _git(container, ["status", "--porcelain"])
    return rc == 0 and bool(out.strip())


async def git_rev_parse(container: str, sha: str) -> str:
    rc, out = await _git(container, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
    if rc != 0:
        _raise_unknown_revision_or_unreachable(sha, out)
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
        _raise_unknown_revision_or_unreachable(sha, out)
    return out


# Exit codes the locked revert script reports back through `flock`.
_REVERT_DIRTY = 2
_REVERT_NOOP = 3


async def git_revert(container: str, sha: str) -> str:
    """Reset the state repo to *sha*, returning "reverted" or "noop".

    The whole decision — is *sha* already HEAD, is the tree clean, reset —
    runs inside the flock the auto-committer also takes. Reading HEAD outside
    the lock let the committer commit in the gap, so a caller that asked to
    restore what was HEAD a moment ago got "noop" while the tree sat on the
    committer's new commit.
    """
    resolved = await git_rev_parse(container, sha)
    if not await git_merge_base_is_ancestor(container, resolved):
        raise NotAncestorError(f"{sha} is not an ancestor of HEAD")
    script = (
        f"head=$(git -C {_REPO_PATH} rev-parse HEAD) || exit 1; "
        f'test "$head" = {resolved} && exit {_REVERT_NOOP}; '
        f"dirty=$(git -C {_REPO_PATH} status --porcelain) || exit 1; "
        f'test -n "$dirty" && exit {_REVERT_DIRTY}; '
        f"git -C {_REPO_PATH} reset --hard {resolved}"
    )
    rc, out = await exec_in_container(
        container,
        ["bash", "-c", f"flock {_STATE_LOCK_PATH} -c {script!r}"],
    )
    if rc == _REVERT_NOOP:
        return "noop"
    if rc == _REVERT_DIRTY:
        raise DirtyTreeError("dirty_tree: working tree has uncommitted changes")
    if rc != 0:
        # The reset itself failed: a corrupt index, an unwritable .git, a
        # missing object. Its own class, because calling that "container
        # unreachable" would misdescribe a repo-state problem, and a bare
        # RuntimeError would surface it as 404 "unknown revision".
        raise GitOperationError(f"git revert failed: {out.strip() or f'rc={rc}'}")
    return "reverted"
