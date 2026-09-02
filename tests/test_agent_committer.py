"""Tests for the agent state auto-committer script."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest


_COMMITTER_PATH = (
    os.path.dirname(__file__).replace("tests", "tinyagentos") + "/scripts/agent_committer.py"
)


def _load_committer(repo_path: str, interval: int = 1):
    spec = importlib.util.spec_from_file_location("agent_committer", _COMMITTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.REPO_PATH = repo_path
    mod.INTERVAL = interval
    return mod


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True, capture_output=True)


class TestAgentCommitter:
    def test_commit_creates_commit_for_new_file(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("*.secret\n.env\n*token*\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
        )

        committer = _load_committer(str(tmp_path))
        (tmp_path / "hello.txt").write_text("hello")
        committer._commit()

        rc, out, _ = committer._git("log", "--oneline")
        assert rc == 0
        assert "auto:" in out
        stat = committer._git("show", "--stat", "HEAD")[1]
        assert "hello.txt" in stat

    def test_gitignored_secret_not_committed(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("*.secret\n.env\n*token*\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
        )

        committer = _load_committer(str(tmp_path))
        (tmp_path / ".env").write_text("SECRET=abc")
        (tmp_path / "token.rsa").write_text("key")
        committer._commit()

        _, log_out, _ = committer._git("log", "--all", "--stat")
        assert ".env" not in log_out
        assert "token.rsa" not in log_out

    def test_no_commit_when_clean(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("")
        subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
        )

        committer = _load_committer(str(tmp_path))
        committer._commit()

        rc, out, _ = committer._git("log", "--oneline")
        assert rc == 0
        lines = out.strip().splitlines()
        assert len(lines) == 1
        assert lines[0].endswith("initial")
