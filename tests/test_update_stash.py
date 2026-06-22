"""The update flow must not 500 on a dirty tracked source file: it stashes the
local edit (recoverable) so `git pull --ff-only` can proceed."""

import subprocess

import pytest

from tinyagentos.routes.settings import _stash_local_source_changes


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


@pytest.mark.asyncio
async def test_clean_tree_no_stash(repo):
    assert await _stash_local_source_changes(repo) is False
    out = subprocess.run(["git", "stash", "list"], cwd=repo, capture_output=True, text=True).stdout
    assert out.strip() == ""


@pytest.mark.asyncio
async def test_dirty_tracked_file_is_stashed(repo):
    (repo / "src.py").write_text("x = 2  # local edit\n")
    assert await _stash_local_source_changes(repo) is True
    # working tree is clean again (so ff-only pull would succeed)
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                            cwd=repo, capture_output=True, text=True).stdout
    assert status.strip() == ""
    # and the edit is recoverable, not lost
    stash = subprocess.run(["git", "stash", "list"], cwd=repo, capture_output=True, text=True).stdout
    assert "auto-update" in stash


@pytest.mark.asyncio
async def test_untracked_files_are_not_stashed(repo):
    (repo / "data_dir_artifact").write_text("runtime\n")  # untracked
    assert await _stash_local_source_changes(repo) is False
    assert (repo / "data_dir_artifact").exists()  # left in place
