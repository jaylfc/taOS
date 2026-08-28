#!/usr/bin/env python3
"""Tests for merge attribution audit and reconciliation.

Three acceptance proofs:
(a) A merge with no audit line makes reconciliation go RED naming the
    unmatched sha, rc captured directly.
(b) CONTROL: a normal fleet merge with an audit line reconciles clean, rc=0.
(c) Deleting the audit line for a real merge makes reconciliation go RED again,
    proving the check is load-bearing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_merge_attribution as cma  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "branch", "-M", "main")


def _commit_file(repo: Path, rel_path: str, content: str, message: str) -> str:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)
    _git(repo, "commit", "-m", message)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_merge(repo: Path, branch: str) -> str:
    """Merge branch into current HEAD and return the merge commit SHA."""
    _git(repo, "merge", branch, "--no-edit")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def setup_diverged_repo(repo: Path) -> None:
    """Set up a repo with diverged main and feature branches."""
    _init_repo(repo)
    _commit_file(repo, "README.md", "# Main\n", "initial")
    _git(repo, "checkout", "-b", "feature")
    _commit_file(repo, "feat.txt", "feature\n", "add feature")
    _git(repo, "checkout", "main")
    _commit_file(repo, "main.txt", "main work\n", "main work")


def _write_audit(audit_file: Path, sha: str) -> None:
    entry = {
        "actor": "test-agent",
        "repo": "test-org/test-repo",
        "pr": 1,
        "sha": sha,
        "merged_by": "test-agent",
        "timestamp": "2026-08-27T11:29:48Z",
        "script": "gate_merge.sh",
    }
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    with audit_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


class TestMergeAttributionReconciliation:
    """Proofs for the merge attribution audit mechanism."""

    def test_merge_without_audit_line_fails_reconciliation(
        self, tmp_path: Path,
    ) -> None:
        """(a) A merge with NO audit line makes reconciliation RED."""
        repo = tmp_path / "repo"
        setup_diverged_repo(repo)

        merge_sha = _make_merge(repo, "feature")

        audit_file = tmp_path / "audit.jsonl"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/check_merge_attribution.py"),
                "--repo", str(repo),
                "--audit-file", str(audit_file),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == cma.EXIT_FAIL
        assert merge_sha[:12] in result.stdout

    def test_merge_with_audit_line_passes_reconciliation(
        self, tmp_path: Path,
    ) -> None:
        """(b) CONTROL: a normal fleet merge reconciles clean, rc=0."""
        repo = tmp_path / "repo"
        setup_diverged_repo(repo)

        merge_sha = _make_merge(repo, "feature")

        audit_file = tmp_path / "audit.jsonl"
        _write_audit(audit_file, merge_sha)

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/check_merge_attribution.py"),
                "--repo", str(repo),
                "--audit-file", str(audit_file),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == cma.EXIT_OK

    def test_deleting_audit_line_makes_reconciliation_fail(
        self, tmp_path: Path,
    ) -> None:
        """(c) Deleting the audit line for a real merge makes reconciliation RED."""
        repo = tmp_path / "repo"
        setup_diverged_repo(repo)

        merge_sha = _make_merge(repo, "feature")

        audit_file = tmp_path / "audit.jsonl"
        _write_audit(audit_file, merge_sha)

        # Verify it passes first
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/check_merge_attribution.py"),
                "--repo", str(repo),
                "--audit-file", str(audit_file),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == cma.EXIT_OK

        # Now delete the audit line
        audit_file.write_text("", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/check_merge_attribution.py"),
                "--repo", str(repo),
                "--audit-file", str(audit_file),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == cma.EXIT_FAIL
        assert merge_sha[:12] in result.stdout
