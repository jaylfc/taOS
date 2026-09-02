#!/usr/bin/env python3
"""Debounced auto-committer for agent state repos.

Runs inside the agent container. Watches the state repo and commits
dirty trees on a fixed interval with a timestamp + changed-file-summary
message. No LLM involvement.
"""
from __future__ import annotations

import os
import subprocess
import time


REPO_PATH = os.environ.get("AGENT_STATE_REPO", "/root")
INTERVAL = int(os.environ.get("COMMIT_INTERVAL", "300"))


def _git(*args: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", "-C", REPO_PATH, *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _is_dirty() -> bool:
    rc, out, _ = _git("status", "--porcelain")
    return rc == 0 and bool(out.strip())


def _changed_summary() -> str:
    rc, out, _ = _git("diff", "--cached", "--stat")
    if rc != 0 or not out.strip():
        rc, out, _ = _git("diff", "--stat")
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    if not lines:
        return "auto-commit"
    # Exclude the Git stat footer (e.g., "2 files changed")
    if lines and "files changed" in lines[-1]:
        lines = lines[:-1]
    if len(lines) == 1:
        return lines[0]
    return f"{len(lines)} files changed"


def _commit() -> None:
    if not _is_dirty():
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    summary = _changed_summary()
    message = f"auto: {ts} | {summary}"
    _git("add", "-A")
    _git("commit", "-m", message)


def main() -> None:
    while True:
        try:
            _commit()
        except Exception:
            pass
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
