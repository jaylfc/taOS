#!/usr/bin/env python3
"""Debounced auto-committer for agent state repos.

Runs inside the agent container. Watches the state repo and commits
dirty trees on a fixed interval with a timestamp + changed-file-summary
message. No LLM involvement.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import time


REPO_PATH = os.environ.get("AGENT_STATE_REPO", "/root")
INTERVAL = int(os.environ.get("COMMIT_INTERVAL", "300"))
_STATE_LOCK_PATH = os.environ.get("AGENT_STATE_LOCK", "/tmp/agent_state.lock")


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
    rc, out, _ = _git("diff", "--cached", "--name-only")
    if rc != 0 or not out.strip():
        rc, out, _ = _git("diff", "--name-only")
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    if not lines:
        return "auto-commit"
    if len(lines) == 1:
        return lines[0]
    return f"{len(lines)} files changed"


def _commit() -> None:
    fd = os.open(_STATE_LOCK_PATH, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if not _is_dirty():
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        summary = _changed_summary()
        message = f"auto: {ts} | {summary}"
        rc, out, err = _git("add", "-A")
        if rc != 0:
            raise RuntimeError(f"git add failed: {err or out}")
        rc, out, err = _git("commit", "-m", message)
        if rc != 0:
            raise RuntimeError(f"git commit failed: {err or out}")
    finally:
        os.close(fd)


def main() -> None:
    while True:
        try:
            _commit()
        except Exception as exc:
            import sys
            print(f"committer error: {exc}", file=sys.stderr)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
