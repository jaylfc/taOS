"""Update rollback state for taOS.

Before every update, the updater records the exact branch + commit it is leaving
so a later ``taos rollback`` can restore BOTH (the previous version and the
previous branch, even if both changed). The record is a tiny ``key='value'``
text file so ``scripts/rollback.sh`` can read it with no Python and no
dashboard, which is the whole point: rollback must work when an update has
broken the app.

The file is DATA, never code. It lives in the install dir, which the installer
chowns to the ``taos`` service account, and ``scripts/rollback.sh`` escalates
with ``sudo`` when it restarts the unit -- so both ends parse it line by line
and accept ``prev_sha`` only when it is a full hex object name. Anything else
is treated as "no usable record", which sends the script to its recovery-tag
fallback instead of dead-ending.

File: ``<project_dir>/.taos-rollback`` (single record, overwritten each update).
"""

from __future__ import annotations

import re
from pathlib import Path

ROLLBACK_FILE = ".taos-rollback"

# A recorded commit is a FULL git object name and nothing else: the writer
# records ``git rev-parse HEAD``, which is 40 hex (64 in a sha256 checkout) and
# never abbreviated. A short value is therefore a truncated or forged record,
# not a legitimate prefix. Kept in sync with sha_safe() in scripts/rollback.sh.
# Matched with fullmatch(), never match(): Python's `$` also matches just before
# a final newline, so `<40 hex>\n` would pass here while bash's `=~` in
# sha_safe() rejects it -- the writer would record a value the shell then
# refuses, which loses the rollback target instead of reporting anything.
_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")

# Characters git bans anywhere in a ref name: ASCII control characters, space,
# and ~ ^ : ? * [ \ (git-check-ref-format(1)).
_REF_BANNED = frozenset("~^:?*[\\ \x7f") | frozenset(chr(c) for c in range(0x20))


def _ref_safe(name: str) -> bool:
    """Would ``scripts/rollback.sh`` restore a branch by this name?

    A reimplementation of ``git check-ref-format refs/heads/<name>`` plus the
    one rule that is ours rather than git's: a name may not start with a dash,
    because ``git checkout -B --force <sha>`` reads it as an option while git
    itself calls ``refs/heads/--force`` a perfectly valid ref. The shell end
    asks git directly; ``tests/test_rollback.py`` pins this copy to the same
    answers so the two readers cannot drift apart.
    """
    if not name or name.startswith("-"):
        return False
    if ".." in name or "@{" in name or name.endswith("."):
        return False
    if any(ch in _REF_BANNED for ch in name):
        return False
    # Slash-separated components: none empty (which also covers a leading or
    # trailing slash and a doubled one), none starting with '.', none ending
    # in '.lock'.
    return all(
        part and not part.startswith(".") and not part.endswith(".lock")
        for part in name.split("/")
    )


def _shq(value: str) -> str:
    """Single-quote a value for the ``key='value'`` record format."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def record_pre_update(project_dir, *, branch: str, sha: str, ts: int) -> Path:
    """Write the pre-update branch + commit so a rollback can restore both.

    Overwrites any prior record: rollback targets the state immediately before
    the most recent update, which is the one a user would want to undo.

    Raises ``ValueError`` for a ``sha`` that is not a full git object name or a
    ``branch`` carrying a newline (which would forge a second record line).
    The caller records best-effort, so a rejected write simply leaves the
    rollback script on its recovery-tag fallback rather than on a bad target.
    """
    if not _SHA_RE.fullmatch(str(sha)):
        raise ValueError(f"rollback sha is not a git object name: {sha!r}")
    if "\n" in str(branch) or "\r" in str(branch):
        raise ValueError(f"rollback branch contains a newline: {branch!r}")
    path = Path(project_dir) / ROLLBACK_FILE
    path.write_text(
        "# taOS rollback target -- the branch + commit the last update left.\n"
        "# Data only: scripts/rollback.sh parses these lines, it never sources them.\n"
        f"prev_branch={_shq(branch)}\n"
        f"prev_sha={_shq(sha)}\n"
        f"prev_ts={_shq(ts)}\n"
    )
    return path


def read_rollback_target(project_dir) -> dict | None:
    """Read the recorded rollback target, or None if there is no usable record.

    Returns ``{"branch": str, "sha": str, "ts": str}``. Parses the simple
    ``key='value'`` lines without sourcing (so it is safe to call on any input),
    and applies exactly the rules scripts/rollback.sh applies to the same file:
    a ``prev_sha`` that is not a full object name makes the whole record
    unusable (None), while a ``prev_branch`` git would refuse costs only the
    branch and comes back as ``""`` -- restoring the commit alone still beats
    not rolling back.
    """
    path = Path(project_dir) / ROLLBACK_FILE
    if not path.is_file():
        return None
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        val = raw.strip()
        if len(val) >= 2 and val[0] == val[-1] == "'":
            val = val[1:-1].replace("'\\''", "'")
        out[key.strip()] = val
    if not _SHA_RE.fullmatch(out.get("prev_sha", "")):
        return None
    branch = out.get("prev_branch", "")
    return {
        "branch": branch if _ref_safe(branch) else "",
        "sha": out["prev_sha"],
        "ts": out.get("prev_ts", ""),
    }
