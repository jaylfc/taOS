#!/usr/bin/env python3
"""Tests for the evil-merge guard (scripts/check_evil_merge.py).

Each integration test builds a synthetic git repo in a temp directory,
creates two branches with contradictory test changes, merges them, and
then calls check_evil_merge() directly against the merge result.  The
RED case hand-resolves a tests/ file to content matching neither parent;
the three GREEN controls keep the resolution clean.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_evil_merge as cem  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _git_capture(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "branch", "-M", "main")


def _commit_file(repo: Path, rel_path: str, content: str, message: str) -> None:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)
    _git(repo, "commit", "-m", message)


def _branch(repo: Path, name: str) -> None:
    _git(repo, "branch", name)


def _checkout(repo: Path, name: str) -> None:
    _git(repo, "checkout", "-q", name)


def _get_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _git_merge(repo: Path, branch: str) -> None:
    """Run git merge, allowing conflicts (exit 1)."""
    result = subprocess.run(
        ["git", "merge", branch, "--no-edit"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr,
        )


def _resolve_and_commit(repo: Path, rel_path: str, content: str) -> None:
    (repo / rel_path).write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)
    _git(repo, "commit", "--no-edit")


# ---------------------------------------------------------------------------
# RED case + GREEN controls
# ---------------------------------------------------------------------------


class TestEvilMergeGuard:
    """RED case and three GREEN controls."""

    def test_evil_merge_invents_test_content_neither_parent(self, tmp_path: Path):
        """RED: two branches assert contradictory outcomes for the same
        scenario; the merge resolution invents test content matching neither
        parent.  The guard must fail and name the path."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n",
            "test: add test_principal",
        )

        _branch(repo, "side-a")
        _checkout(repo, "side-a")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_accepted()\n",
            "feat: assert accepted",
        )

        _checkout(repo, "main")
        _branch(repo, "side-b")
        _checkout(repo, "side-b")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_rejected()\n",
            "feat: assert rejected",
        )

        _checkout(repo, "main")
        _git_merge(repo, "side-a")
        _git_merge(repo, "side-b")

        neither = "def test_principal():\n    assert check_principal()\n"
        _resolve_and_commit(repo, "tests/test_widget.py", neither)

        violations = cem.check_evil_merge(repo)

        assert len(violations) == 1
        v = violations[0]
        assert v.path == "tests/test_widget.py"
        assert v.merge_hash
        assert v.parent1_hash
        assert v.parent2_hash
        assert v.merge_tree_hash is not None
        assert v.merge_hash != v.parent1_hash
        assert v.merge_hash != v.parent2_hash

    def test_merge_taking_one_side_wholesale_stays_green(self, tmp_path: Path):
        """CONTROL A: both branches touch different, non-overlapping parts of
        the same test file, producing a clean auto-merge.  Resolving by
        keeping side-a's content wholesale matches what git would produce,
        so the guard passes."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n\ndef test_secondary():\n    pass\n",
            "test: add two tests",
        )

        _branch(repo, "side-a")
        _checkout(repo, "side-a")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_accepted()\n\ndef test_secondary():\n    pass\n",
            "feat: assert accepted on side-a",
        )

        _checkout(repo, "main")
        _branch(repo, "side-b")
        _checkout(repo, "side-b")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n\ndef test_secondary():\n    assert principal_is_rejected()\n",
            "feat: assert rejected on side-b",
        )

        _checkout(repo, "main")
        _git_merge(repo, "side-a")
        _git_merge(repo, "side-b")

        violations = cem.check_evil_merge(repo)
        assert violations == []

    def test_ordinary_non_merge_commit_stays_green(self, tmp_path: Path):
        """CONTROL B: an ordinary non-merge commit touching the same test
        file.  The guard exits clean because HEAD is not a merge commit."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n",
            "test: add test_principal",
        )
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_accepted()\n",
            "feat: assert accepted",
        )

        violations = cem.check_evil_merge(repo)
        assert violations == []

    def test_merge_with_untouched_tests_stays_green(self, tmp_path: Path):
        """CONTROL C: a merge whose tests/ files are untouched by the
        resolution.  The guard exits clean."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n",
            "test: add test_principal",
        )
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "feat: add function_a",
        )

        _branch(repo, "side-a")
        _checkout(repo, "side-a")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    return 'a'\n",
            "feat: update function_a on side-a",
        )

        _checkout(repo, "main")
        _branch(repo, "side-b")
        _checkout(repo, "side-b")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    return 'b'\n",
            "feat: update function_a on side-b",
        )

        _checkout(repo, "main")
        _git_merge(repo, "side-a")
        _git_merge(repo, "side-b")

        violations = cem.check_evil_merge(repo)
        assert violations == []

    def test_clean_auto_merge_different_parts_of_same_file_stays_green(self, tmp_path: Path):
        """CONTROL D: two branches edit different, non-overlapping parts of
        the same test file.  Git auto-merges cleanly with no conflict; the
        guard must stay green because the resolution matches what git would
        have produced on its own.  This is the case that previously produced
        a false positive under the 'matches neither parent' predicate."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n\ndef test_secondary():\n    pass\n",
            "test: add two tests",
        )

        _branch(repo, "side-a")
        _checkout(repo, "side-a")
        _commit_file(
            repo, "tests/test_widget.py",
            "import pytest\n\n"
            "def test_principal():\n    pass\n\n"
            "def test_secondary():\n    pass\n",
            "feat: add import on side-a",
        )

        _checkout(repo, "main")
        _branch(repo, "side-b")
        _checkout(repo, "side-b")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n\n"
            "def test_secondary():\n    assert True\n",
            "feat: add body on side-b",
        )

        _checkout(repo, "main")
        _git_merge(repo, "side-a")
        _git_merge(repo, "side-b")

        violations = cem.check_evil_merge(repo)
        assert violations == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEvilMergeGuardEdgeCases:
    """Edge cases for the evil-merge guard."""

    def test_non_merge_commit_returns_empty(self, tmp_path: Path):
        """A regular commit (single parent) returns no violations."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n",
            "test: add test_principal",
        )

        violations = cem.check_evil_merge(repo)
        assert violations == []

    def test_no_test_files_returns_empty(self, tmp_path: Path):
        """A merge commit with no tests/ files returns no violations."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "feat: add function_a",
        )

        _branch(repo, "side-a")
        _checkout(repo, "side-a")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    return 'a'\n",
            "feat: update on side-a",
        )

        _checkout(repo, "main")
        _branch(repo, "side-b")
        _checkout(repo, "side-b")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    return 'b'\n",
            "feat: update on side-b",
        )

        _checkout(repo, "main")
        _git_merge(repo, "side-a")
        _git_merge(repo, "side-b")

        violations = cem.check_evil_merge(repo)
        assert violations == []

    def test_new_test_file_in_merge_inventing_content(self, tmp_path: Path):
        """A test file invented during merge resolution (not present in either
        parent) is flagged as an evil merge."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n",
            "test: add test_principal",
        )

        _branch(repo, "side-a")
        _checkout(repo, "side-a")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_accepted()\n",
            "feat: assert accepted",
        )

        _checkout(repo, "main")
        _branch(repo, "side-b")
        _checkout(repo, "side-b")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_rejected()\n",
            "feat: assert rejected",
        )

        _checkout(repo, "main")
        _git_merge(repo, "side-a")
        _git_merge(repo, "side-b")

        # Remove the existing test file and invent a brand-new one.
        (repo / "tests/test_widget.py").unlink()
        _git(repo, "add", "tests/test_widget.py")
        _resolve_and_commit(repo, "tests/test_new.py", "def test_invented():\n    assert True\n")

        violations = cem.check_evil_merge(repo)

        assert len(violations) == 1
        assert violations[0].path == "tests/test_new.py"

    def test_head_ref_parameter(self, tmp_path: Path):
        """The head_ref parameter lets the caller point at a specific ref
        rather than trusting the checkout's HEAD."""
        repo = tmp_path / "repo"
        _init_repo(repo)

        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    pass\n",
            "test: add test_principal",
        )

        _branch(repo, "side-a")
        _checkout(repo, "side-a")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_accepted()\n",
            "feat: assert accepted",
        )

        _checkout(repo, "main")
        _branch(repo, "side-b")
        _checkout(repo, "side-b")
        _commit_file(
            repo, "tests/test_widget.py",
            "def test_principal():\n    assert principal_is_rejected()\n",
            "feat: assert rejected",
        )

        _checkout(repo, "main")
        _git_merge(repo, "side-a")
        _git_merge(repo, "side-b")

        neither = "def test_principal():\n    assert check_principal()\n"
        _resolve_and_commit(repo, "tests/test_widget.py", neither)
        evil_merge_sha = _get_head(repo)

        # Move HEAD away from the evil merge to prove head_ref is honoured.
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "feat: unrelated commit after evil merge",
        )

        # When head_ref points at the evil merge the guard still finds it.
        violations = cem.check_evil_merge(repo, head_ref=evil_merge_sha)
        assert len(violations) == 1
        assert violations[0].path == "tests/test_widget.py"
