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
and accept ``prev_sha`` only when it is a hex object name. Anything else is
treated as "no usable record", which sends the script to its recovery-tag
fallback instead of dead-ending.

File: ``<project_dir>/.taos-rollback`` (single record, overwritten each update).
"""

from __future__ import annotations

import re
from pathlib import Path

ROLLBACK_FILE = ".taos-rollback"

# A recorded commit is a git object name and nothing else. Kept in sync with the
# same check in scripts/rollback.sh so both readers agree on what is usable.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _shq(value: str) -> str:
    """Single-quote a value for the ``key='value'`` record format."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def record_pre_update(project_dir, *, branch: str, sha: str, ts: int) -> Path:
    """Write the pre-update branch + commit so a rollback can restore both.

    Overwrites any prior record: rollback targets the state immediately before
    the most recent update, which is the one a user would want to undo.

    Raises ``ValueError`` for a ``sha`` that is not a git object name or a
    ``branch`` carrying a newline (which would forge a second record line).
    The caller records best-effort, so a rejected write simply leaves the
    rollback script on its recovery-tag fallback rather than on a bad target.
    """
    if not _SHA_RE.match(str(sha)):
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
    and rejects a truncated or tampered record whose ``prev_sha`` is not a hex
    object name -- the same rule scripts/rollback.sh applies.
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
    if "prev_branch" not in out or not _SHA_RE.match(out.get("prev_sha", "")):
        return None
    return {"branch": out["prev_branch"], "sha": out["prev_sha"], "ts": out.get("prev_ts", "")}
