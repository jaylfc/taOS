"""Tests for tinyagentos.update_runner.

Tests the _run helper directly (unit tests in test_update_runner_run.py).
This file previously held integration tests for update_to_master, which has
been removed as dead code.  The switch_to_branch integration tests live in
tests/test_switch_to_branch.py.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

if not shutil.which("git"):
    pytest.skip("git not on PATH", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


def _configure_repo(repo: Path) -> None:
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.com"], repo)


def _make_repos(tmp_path: Path) -> tuple[Path, Path]:
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    _git(["init", "--bare", "-b", "master", str(upstream)], tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["clone", str(upstream), str(seed)], tmp_path)
    _configure_repo(seed)
    _git(["checkout", "-B", "master"], seed)
    (seed / "README.md").write_text("initial\n")
    _git(["add", "README.md"], seed)
    _git(["commit", "-m", "init"], seed)
    _git(["push", "-u", "origin", "master"], seed)

    local = tmp_path / "local"
    _git(["clone", str(upstream), str(local)], tmp_path)
    _configure_repo(local)

    return upstream, local
