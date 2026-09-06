"""Tests for the agent state auto-committer script."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


_COMMITTER_PATH = str(
    Path(__file__).resolve().parent.parent
    / "tinyagentos"
    / "scripts"
    / "agent_committer.py"
)


def _load_committer(repo_path, tmp_path, interval: int = 1):
    spec = importlib.util.spec_from_file_location("agent_committer", _COMMITTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REPO_PATH = str(repo_path)
    mod.INTERVAL = interval
    # The lock file must live outside the repo under test: inside it, it
    # would show up as an untracked file and would either get committed by
    # `git add -A` or keep the tree permanently "dirty" for `_is_dirty()`.
    mod._STATE_LOCK_PATH = str(tmp_path / "agent_state.lock")
    return mod


def _init_repo(repo_path: Path):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_path, check=True, capture_output=True)


class TestAgentCommitter:
    def test_commit_creates_commit_for_new_file(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / ".gitignore").write_text("*.secret\n.env\n*token*\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
        )

        committer = _load_committer(repo, tmp_path)
        (repo / "hello.txt").write_text("hello")
        committer._commit()

        rc, out, _ = committer._git("log", "--oneline")
        assert rc == 0
        assert "auto:" in out
        stat = committer._git("show", "--stat", "HEAD")[1]
        assert "hello.txt" in stat

    def test_gitignored_secret_not_committed(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / ".gitignore").write_text("*.secret\n.env\n*token*\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
        )

        committer = _load_committer(repo, tmp_path)
        (repo / ".env").write_text("SECRET=abc")
        (repo / "token.rsa").write_text("key")
        committer._commit()

        _, log_out, _ = committer._git("log", "--all", "--stat")
        assert ".env" not in log_out
        assert "token.rsa" not in log_out

    def test_no_commit_when_clean(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / ".gitignore").write_text("")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
        )

        committer = _load_committer(repo, tmp_path)
        committer._commit()

        rc, out, _ = committer._git("log", "--oneline")
        assert rc == 0
        lines = out.strip().splitlines()
        assert len(lines) == 1
        assert lines[0].endswith("initial")

    def test_gitignored_ssh_key_not_committed(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / ".gitignore").write_text("*.secret\n.env\n*token*\n.ssh/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
        )

        committer = _load_committer(repo, tmp_path)
        (repo / ".ssh").mkdir()
        (repo / ".ssh" / "id_rsa").write_text("fake-key")
        committer._commit()

        _, log_out, _ = committer._git("log", "--all", "--stat")
        assert ".ssh" not in log_out
        assert "id_rsa" not in log_out

    def test_commit_message_names_a_new_untracked_file(self, tmp_path):
        """`_changed_summary` must be computed from what `git add -A` staged,
        not from what was staged before it ran: an untracked file (the
        common agent change — a new workspace/ file) shows up in neither
        `git diff --cached --name-only` nor `git diff --name-only` before
        staging, so a pre-add summary always falls back to "auto-commit" and
        the commit message loses the file name for exactly this case."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        (repo / ".gitignore").write_text("")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
        )

        committer = _load_committer(repo, tmp_path)
        (repo / "workspace_notes.md").write_text("new untracked file")
        committer._commit()

        rc, out, _ = committer._git("log", "-1", "--format=%s")
        assert rc == 0
        subject = out.strip()
        assert "workspace_notes.md" in subject, (
            f"commit subject does not name the new file: {subject!r}"
        )
