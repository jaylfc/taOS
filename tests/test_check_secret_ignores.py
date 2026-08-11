"""Tests for the secret-material ignore guard (scripts/check_secret_ignores.py).

The guard asserts two things on the branch under test:
  1. Every REQUIRED_PATTERN appears as an exact active rule line in .gitignore.
  2. Every SECRET_PATH is reported ignored by `git check-ignore`.

The headline guarantee from tsk-laezfg is "PROVEN to fail when a pattern is
removed": a parametrized test builds a synthetic repo from a copy of the real
.gitignore, drops ONE required pattern line, and asserts the guard goes red. A
companion real-tree test asserts the committed .gitignore on this branch is
green, so the gate is a live regression guard and not dead code.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; make it importable like the other scripts/*.py
# gate tests (see tests/test_check_deleted_symbols.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_secret_ignores as csi  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_GITIGNORE = REPO_ROOT / ".gitignore"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "commit.gpgsign", "false")
    # No inherited global/system ignores -- the only ignores in the synthetic
    # repo come from the .gitignore we write, so the gate's path assertions stay
    # deterministic.
    _git(repo, "config", "core.excludesFile", "/dev/null")
    _git(repo, "branch", "-M", "main")


def _commit_gitignore(repo: Path, text: str) -> None:
    (repo / ".gitignore").write_text(text, encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "gitignore")


def _gitignore_without(text: str, pattern: str) -> str:
    """Return `text` with the exact active rule line `pattern` removed."""
    kept: list[str] = []
    for raw in text.splitlines():
        if raw.strip() == pattern:
            continue
        kept.append(raw)
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


# ---------------------------------------------------------------------------
# Pattern-presence check (pure, no git)
# ---------------------------------------------------------------------------


class TestCheckPatterns:
    def test_active_rule_lines_drops_comments_and_blanks(self):
        text = (
            "# a comment *.key\n"
            "\n"
            "  *.key  \n"
            "data/hub/\n"
        )
        assert csi._active_rule_lines(text) == ["*.key", "data/hub/"]

    def test_all_required_patterns_present_on_real_tree(self):
        text = REAL_GITIGNORE.read_text(encoding="utf-8")
        assert csi.check_patterns(text) == []

    @pytest.mark.parametrize("pattern", csi.REQUIRED_PATTERNS)
    def test_removing_a_single_pattern_is_detected(self, pattern: str):
        """Dropping ONE required rule line turns the pattern check red for
        exactly that pattern -- this is the core 'remove a pattern -> red'
        proof, run against every required pattern."""
        text = _gitignore_without(REAL_GITIGNORE.read_text(encoding="utf-8"), pattern)
        missing = csi.check_patterns(text)
        assert missing == [pattern]

    def test_comment_mention_does_not_satisfy_a_pattern(self):
        """A pattern name in a comment (#-prefixed) must not count as present."""
        text = "# *.key is for key material\ndata/hub/\n"
        assert "data/hub/" not in csi.check_patterns(text)
        assert "*.key" in csi.check_patterns(text)

    def test_narrow_rule_does_not_satisfy_a_root_rule(self):
        """`data/*.key` must not satisfy the master-level `*.key` rule, and vice
        versa -- matching is exact-line, not substring."""
        assert "*.key" in csi.check_patterns("data/*.key\n")
        assert "data/*.key" in csi.check_patterns("*.key\n")

    def test_missing_gitignore_reports_all_patterns(self):
        assert set(csi.REQUIRED_PATTERNS).issubset(set(csi.check_patterns("")))


# ---------------------------------------------------------------------------
# Path-check (git check-ignore), integration with synthetic repos
# ---------------------------------------------------------------------------


class TestCheckPaths:
    def test_secret_paths_ignored_on_real_gitignore(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_gitignore(repo, REAL_GITIGNORE.read_text(encoding="utf-8"))
        assert csi.check_paths(repo) == []

    def test_path_not_ignored_when_pattern_removed(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        text = _gitignore_without(
            REAL_GITIGNORE.read_text(encoding="utf-8"), "*.key"
        )
        _commit_gitignore(repo, text)
        assert "foo.key" in csi.check_paths(repo)

    def test_path_under_ignored_dir_is_ignored(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_gitignore(repo, "data/hub/\nidentity.json\n")
        assert csi.is_path_ignored("data/hub/identity.json", repo)


# ---------------------------------------------------------------------------
# Full guard: green on the real tree, red on a single removed pattern
# ---------------------------------------------------------------------------


class TestCheckSecretIgnores:
    def test_real_tree_passes(self):
        assert csi.check_secret_ignores(REPO_ROOT) == []

    @pytest.mark.parametrize("pattern", csi.REQUIRED_PATTERNS)
    def test_real_gitignore_minus_one_pattern_fails(self, tmp_path: Path, pattern: str):
        """PROVEN red: copy the committed .gitignore, drop ONE required pattern,
        and the guard must report a violation."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        text = _gitignore_without(REAL_GITIGNORE.read_text(encoding="utf-8"), pattern)
        _commit_gitignore(repo, text)

        violations = csi.check_secret_ignores(repo)

        pattern_violations = [v for v in violations if v.kind == "pattern"]
        assert pattern_violations, f"removing {pattern!r} did not trip the pattern guard"
        assert any(v.detail == pattern for v in pattern_violations)

    def test_real_gitignore_missing_key_makes_foo_key_unignored(
        self, tmp_path: Path
    ):
        repo = tmp_path / "repo"
        _init_repo(repo)
        text = _gitignore_without(REAL_GITIGNORE.read_text(encoding="utf-8"), "*.key")
        _commit_gitignore(repo, text)

        violations = csi.check_secret_ignores(repo)

        details = {v.detail for v in violations}
        assert "*.key" in details
        assert "foo.key" in details
