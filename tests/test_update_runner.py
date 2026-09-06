"""Tests for tinyagentos.update_runner.

Uses real git repos in tmpdir — no mocking of git itself.
Each test builds an upstream bare repo + a local clone so origin is real.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# Skip the whole module if git is not available.
if not shutil.which("git"):
    pytest.skip("git not on PATH", allow_module_level=True)

from tinyagentos.update_runner import update_to_master


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
    """Set user.name and user.email so commits don't fail in CI."""
    _git(["config", "user.name", "Test User"], repo)
    _git(["config", "user.email", "test@example.com"], repo)


def _make_repos(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare upstream and a local clone. Return (upstream, local)."""
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    # Force master as the default branch regardless of git global config.
    _git(["init", "--bare", "-b", "master", str(upstream)], tmp_path)

    # Seed upstream with an initial commit via a temp working tree
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["clone", str(upstream), str(seed)], tmp_path)
    _configure_repo(seed)
    # Ensure we're on master (older git may still need explicit branch).
    _git(["checkout", "-B", "master"], seed)
    (seed / "README.md").write_text("initial\n")
    _git(["add", "README.md"], seed)
    _git(["commit", "-m", "init"], seed)
    _git(["push", "-u", "origin", "master"], seed)

    # Clone for the test subject
    local = tmp_path / "local"
    _git(["clone", str(upstream), str(local)], tmp_path)
    _configure_repo(local)

    return upstream, local


def _add_remote_commit(upstream: Path, tmp_path: Path, filename: str = "remote.txt", content: str = "remote\n") -> None:
    """Push a new commit to the bare upstream."""
    helper = tmp_path / "helper"
    if not helper.exists():
        _git(["clone", str(upstream), str(helper)], tmp_path)
        _configure_repo(helper)
    else:
        _git(["pull", "--ff-only"], helper)
    (helper / filename).write_text(content)
    _git(["add", filename], helper)
    _git(["commit", "-m", f"add {filename}"], helper)
    _git(["push", "origin", "master"], helper)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clean_fast_forward(tmp_path: Path):
    """Remote has 1 commit ahead, local on master clean — should FF cleanly."""
    upstream, local = _make_repos(tmp_path)
    _add_remote_commit(upstream, tmp_path)

    result = await update_to_master(local)

    expected_sha = subprocess.run(
        ["git", "rev-parse", "origin/master"],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout.strip()

    assert result.new_sha == expected_sha
    assert result.recovery_tag is None
    assert result.stash_ref is None
    assert result.stash_restored is False
    assert result.branch_tag is None
    assert "Updated" in result.message


@pytest.mark.asyncio
async def test_non_master_branch(tmp_path: Path):
    """Local on feature/foo, remote ahead — should tag branch and switch to master."""
    upstream, local = _make_repos(tmp_path)

    # Create and switch to a feature branch
    _git(["checkout", "-b", "feature/foo"], local)
    _add_remote_commit(upstream, tmp_path)

    result = await update_to_master(local)

    # Should have created a branch tag matching the pattern
    assert result.branch_tag is not None
    assert result.branch_tag.startswith("taos-pre-update-feature-foo-")

    # HEAD should now be on master at origin/master
    branch_out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch_out == "master"

    expected_sha = subprocess.run(
        ["git", "rev-parse", "origin/master"],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert result.new_sha == expected_sha

    # Tag must exist in the repo
    tags = subprocess.run(
        ["git", "tag", "-l", result.branch_tag],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert tags == result.branch_tag


@pytest.mark.asyncio
async def test_dirty_working_tree(tmp_path: Path):
    """Dirty tracked file — should stash, FF, then restore the change."""
    upstream, local = _make_repos(tmp_path)
    _add_remote_commit(upstream, tmp_path, filename="other.txt")

    # Dirty the working tree (different file from remote commit)
    (local / "README.md").write_text("local tweak\n")

    result = await update_to_master(local)

    assert result.stash_ref is not None
    assert result.stash_restored is True
    assert result.recovery_tag is None
    # The local edit should be back on disk
    assert (local / "README.md").read_text() == "local tweak\n"
    assert "Local changes restored from stash" in result.message


@pytest.mark.asyncio
async def test_diverged_history(tmp_path: Path):
    """Local has an extra commit, remote also has a different commit — should tag and hard-reset."""
    upstream, local = _make_repos(tmp_path)

    # Local commit (diverge)
    (local / "local_only.txt").write_text("local commit\n")
    _git(["add", "local_only.txt"], local)
    _git(["commit", "-m", "local diverge"], local)
    local_diverged_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout.strip()

    # Remote commit (different history)
    _add_remote_commit(upstream, tmp_path, filename="remote_only.txt")

    result = await update_to_master(local)

    assert result.recovery_tag is not None
    assert result.recovery_tag.startswith("taos-pre-update-")

    # HEAD should be at origin/master
    expected_sha = subprocess.run(
        ["git", "rev-parse", "origin/master"],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert result.new_sha == expected_sha

    # The recovery tag must point to the old local sha
    tag_sha = subprocess.run(
        ["git", "rev-parse", result.recovery_tag],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert tag_sha == local_diverged_sha


@pytest.mark.asyncio
async def test_stash_restore_conflict(tmp_path: Path):
    """Local edit conflicts with incoming change — stash pop should fail gracefully."""
    upstream, local = _make_repos(tmp_path)

    # Both local and remote edit the same lines of README.md
    (local / "README.md").write_text("local version\n")
    _add_remote_commit(upstream, tmp_path, filename="README.md", content="remote version\n")

    result = await update_to_master(local)

    # Stash pop should have failed — stash preserved, not restored
    assert result.stash_ref is not None
    assert result.stash_restored is False
    assert "stash" in result.message.lower()

    # Stash must still be listed
    stash_list = subprocess.run(
        ["git", "stash", "list"],
        cwd=str(local), capture_output=True, text=True, check=True
    ).stdout
    assert "taos-update-" in stash_list
