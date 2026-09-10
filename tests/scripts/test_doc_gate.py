"""Tests for the doc-gate defects fixed in tsk-5wcj4k."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_doc_gate.py"
SCRIPTS_DIR = _SCRIPT.parent
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_module():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("check_doc_gate", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_doc_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check_mod():
    return _load_module()


class TestGlobMatch:
    """Mid-pattern ** must match correctly (R31)."""

    def test_docs_star_star_star_md_matches_docs_x_md(self, check_mod) -> None:
        assert check_mod._glob_match("docs/x.md", "docs/**/*.md") is True

    def test_a_star_star_b_matches_a_b(self, check_mod) -> None:
        assert check_mod._glob_match("a/b", "a/**/b") is True


class TestMatchAny:
    def test_docs_star_star_star_md_matches_docs_x_md_via_match_any(
        self, check_mod,
    ) -> None:
        assert check_mod._match_any("docs/x.md", ["docs/**/*.md"]) is True


class TestNonAsciiPathTriggersRule:
    """A changed path containing a non-ASCII character must trigger its doc
    rule (R32)."""

    RULE = {
        "name": "docs-rule",
        "when_changed": ["docs/**"],
        "require_doc": ["docs/agent-manual/*.md"],
        "hint": "docs changed",
        "on_modify": False,
    }

    def test_unicode_path_in_changed_status_triggers_rule(
        self, check_mod,
    ) -> None:
        """A path like docs/café.md in changed_status must match docs/**."""
        changed = [("A", "docs/caf\u00e9.md")]
        failures = check_mod.evaluate_rules(
            changed_status=changed,
            commit_messages=[],
            config={"rules": [self.RULE]},
        )
        assert len(failures) == 1, (
            "expected 1 violation for docs/café.md without matching doc; "
            f"got {failures}"
        )

    def test_parse_name_status_splits_nul_separated_records(
        self, check_mod,
    ) -> None:
        """After the fix git diff -z returns NUL-separated records;
        _parse_name_status must split on \\0, not \\n."""
        from _gitutil import _parse_name_status
        # Simulate two NUL-separated records from `git diff -z --name-status`:
        # A\tpath/added.py\0M\tpath/modified.py\0
        nul = "\0"
        raw = f"A\tpath/added.py{nul}M\tpath/modified.py{nul}"
        result = _parse_name_status(raw)
        assert result == [
            ("A", "path/added.py"),
            ("M", "path/modified.py"),
        ]

    def test_non_ascii_path_not_quoted_by_git_with_core_quote_path_false(
        self, check_mod, tmp_path: Path,
    ) -> None:
        """With -c core.quotePath=false, git returns the raw unicode path."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "docs").mkdir()
        (repo / "docs" / "caf\u00e9.md").write_text("# cafe\n")
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "docs/caf\u00e9.md"], cwd=repo, check=True, capture_output=True)

        from _gitutil import git_changed_staged
        changed = git_changed_staged(repo)

        paths = [p for _, p in changed]
        assert any("caf\u00e9" in p for p in paths), (
            f"expected unicode path in {paths}"
        )
        # Confirm no quotes around the path
        for _, p in changed:
            assert not (p.startswith('"') and p.endswith('"')), (
                f"path should not be git-quoted: {p!r}"
            )


class TestStagedDiffGateNonAscii:
    """The full staged-diff pipeline must pass unquoted non-ASCII paths."""

    def test_git_changed_staged_returns_unquoted_non_ascii(
        self, check_mod, tmp_path: Path,
    ) -> None:
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "docs").mkdir()
        (repo / "docs" / "caf\u00e9.md").write_text("# cafe\n")
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "docs/caf\u00e9.md"], cwd=repo, check=True, capture_output=True)

        from _gitutil import git_changed_staged
        changed = git_changed_staged(repo)

        paths = [p for _, p in changed]
        assert any("caf\u00e9" in p and "\"" not in p for p in paths), (
            f"expected unquoted non-ASCII path in {paths}"
        )
