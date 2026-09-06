"""Tests for the update branch-tracking + ancestry helpers.

Regression cover for the bug where the updater hard-coded ``origin/master``,
so a dev/test box (running ahead on ``dev``) was told an older master commit
was "available" and Install Update failed (ff-only pull of master aborts).
"""
import asyncio
import subprocess

import pytest

from tinyagentos.auto_update import update_tracking_branch, remote_is_strictly_ahead


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _sha(cwd, ref="HEAD"):
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=cwd, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "checkout", "-q", "-b", "master")
    (r / "f").write_text("1")
    _git(r, "add", "."); _git(r, "commit", "-qm", "c1")
    return r


def test_tracking_branch_is_checked_out_branch(repo):
    _git(repo, "checkout", "-q", "-b", "dev")
    assert asyncio.run(update_tracking_branch(repo)) == "dev"
    _git(repo, "checkout", "-q", "master")
    assert asyncio.run(update_tracking_branch(repo)) == "master"


def test_tracking_branch_falls_back_to_master_when_detached(repo):
    sha = _sha(repo)
    _git(repo, "checkout", "-q", sha)  # detached HEAD
    assert asyncio.run(update_tracking_branch(repo)) == "master"


def test_strictly_ahead_true_when_current_is_ancestor(repo):
    old = _sha(repo)
    (repo / "f").write_text("2")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "c2")
    new = _sha(repo)
    assert asyncio.run(remote_is_strictly_ahead(repo, old, new)) is True


def test_not_ahead_when_remote_is_older_or_equal(repo):
    old = _sha(repo)
    (repo / "f").write_text("2")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "c2")
    new = _sha(repo)
    # remote older than current (the dev-box-vs-master case) -> NOT an update
    assert asyncio.run(remote_is_strictly_ahead(repo, new, old)) is False
    # identical -> NOT an update
    assert asyncio.run(remote_is_strictly_ahead(repo, new, new)) is False


def test_not_ahead_when_divergent(repo):
    base = _sha(repo)
    _git(repo, "checkout", "-q", "-b", "dev")
    (repo / "d").write_text("d")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "dev-only")
    dev = _sha(repo)
    _git(repo, "checkout", "-q", "master")
    (repo / "m").write_text("m")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "master-only")
    master = _sha(repo)
    # dev and master diverged from base -> neither is ahead of the other
    assert asyncio.run(remote_is_strictly_ahead(repo, dev, master)) is False
    assert base  # sanity
