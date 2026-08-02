"""Tests for the deleted-symbols guard (scripts/check_deleted_symbols.py).

Each integration test builds a synthetic git repo in a temp directory,
merges a PR branch into base to produce the merge result, checks out the
merge commit, and then calls check_deleted_symbols() directly against the
pre-merge base tip. This proves the check goes RED (fails), GREEN (passes),
and that the Removes-Intentionally trailer waives a named symbol.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# scripts/ is not a package; make it importable the same way the other
# scripts/*.py unit tests do (see tests/test_check_schema_migrations.py).
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_deleted_symbols as cds  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


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


def _delete_file(repo: Path, rel_path: str) -> None:
    full = repo / rel_path
    if full.exists():
        full.unlink()
    _git(repo, "add", rel_path)
    _git(repo, "commit", "-m", f"refactor: delete {rel_path}")


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


# ---------------------------------------------------------------------------
# Core logic unit tests (no git required)
# ---------------------------------------------------------------------------


class TestExtractSymbols:
    def test_top_level_function_and_class(self):
        source = "def foo():\n    pass\n\nclass Bar:\n    pass\n"
        syms = cds._extract_symbols(source, "mod.py")
        assert "mod.py:foo" in syms
        assert syms["mod.py:foo"] == "def"
        assert "mod.py:Bar" in syms
        assert syms["mod.py:Bar"] == "class"

    def test_nested_method_qualified_name(self):
        source = "class Bar:\n    def method(self):\n        pass\n"
        syms = cds._extract_symbols(source, "mod.py")
        assert "mod.py:Bar.method" in syms
        assert syms["mod.py:Bar.method"] == "def"

    def test_nested_class(self):
        source = "class Outer:\n    class Inner:\n        pass\n"
        syms = cds._extract_symbols(source, "mod.py")
        assert "mod.py:Outer.Inner" in syms
        assert syms["mod.py:Outer.Inner"] == "class"

    def test_async_function(self):
        source = "async def afoo():\n    pass\n"
        syms = cds._extract_symbols(source, "mod.py")
        assert "mod.py:afoo" in syms
        assert syms["mod.py:afoo"] == "def"

    def test_syntax_error_returns_empty(self):
        syms = cds._extract_symbols("def (:\n", "mod.py")
        assert syms == {}


class TestParseWaivedSymbols:
    def test_parses_comma_separated(self):
        body = "Some PR description.\n\nRemoves-Intentionally: mod.py:foo, mod.py:Bar.baz"
        waived = cds.parse_waived_symbols(body)
        assert waived == {"mod.py:foo", "mod.py:Bar.baz"}

    def test_no_trailer_returns_empty(self):
        assert cds.parse_waived_symbols("just a description") == set()

    def test_none_body_returns_empty(self):
        assert cds.parse_waived_symbols(None) == set()

    def test_trailer_with_no_symbols(self):
        assert cds.parse_waived_symbols("Removes-Intentionally:") == set()

    def test_multiple_trailer_lines(self):
        body = "Removes-Intentionally: a.py:foo\n\nRemoves-Intentionally: b.py:Bar"
        waived = cds.parse_waived_symbols(body)
        assert waived == {"a.py:foo", "b.py:Bar"}


class TestFindSignalSymbols:
    def test_signal_is_base_minus_head(self):
        base = {"f.py:a": "def", "f.py:b": "def", "f.py:c": "def"}
        head = {"f.py:a": "def", "f.py:b": "def"}
        signal = cds.find_signal_symbols(base, head)
        assert signal == {"f.py:c": "def"}

    def test_no_signal_when_base_equals_head(self):
        base = {"f.py:a": "def"}
        head = {"f.py:a": "def"}
        signal = cds.find_signal_symbols(base, head)
        assert signal == {}

    def test_no_signal_when_head_has_extra(self):
        base = {"f.py:a": "def"}
        head = {"f.py:a": "def", "f.py:b": "def"}
        signal = cds.find_signal_symbols(base, head)
        assert signal == {}


# ---------------------------------------------------------------------------
# Integration tests with synthetic git repos (merge-result model)
# ---------------------------------------------------------------------------


class TestCheckDeletedSymbols:
    def test_deletes_symbol_added_after_merge_base_fails(self, tmp_path: Path):
        """A synthetic PR that deletes a symbol added to dev after its merge
        base FAILS the check and names the symbol and the commit that added
        it."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n",
            "initial: add function_a and function_b",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        # PR branch deletes function_b.
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_b",
        )
        # Merge PR into main to produce the merge result.
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived = cds.check_deleted_symbols(base_tip, repo)

        assert len(violations) == 1
        v = violations[0]
        assert "function_b" in v.symbol
        assert v.added_by != "unknown"
        assert "function_b" in v.added_by
        assert waived == set()

    def test_deletes_own_newly_added_code_passes(self, tmp_path: Path):
        """A PR deleting its own newly-added code PASSES -- the symbol was
        never on dev, so there is nothing to silently delete."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "initial: add function_a",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        # On PR branch: add function_x then remove it.
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_x():\n    pass\n",
            "feat: add function_x",
        )
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_x",
        )
        # Merge PR into main.
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived = cds.check_deleted_symbols(base_tip, repo)

        assert violations == []
        assert waived == set()

    def test_removes_intentionally_trailer_waives(self, tmp_path: Path):
        """The Removes-Intentionally trailer waives a named symbol and the
        waiver is logged (returned in the waived set)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n",
            "initial: add function_a and function_b",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_b",
        )
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        pr_body = "Removes-Intentionally: tinyagentos/foo.py:function_b"
        violations, waived = cds.check_deleted_symbols(base_tip, repo, pr_body=pr_body)

        assert violations == []
        assert "tinyagentos/foo.py:function_b" in waived

    def test_deleted_class_added_after_merge_base_fails(self, tmp_path: Path):
        """Same signal logic for a class, not just a function."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\nclass NewClass:\n    pass\n",
            "initial: add function_a and NewClass",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove NewClass",
        )
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived = cds.check_deleted_symbols(base_tip, repo)

        assert len(violations) == 1
        assert "NewClass" in violations[0].symbol
        assert violations[0].added_by != "unknown"

    def test_deleted_method_added_after_merge_base_fails(self, tmp_path: Path):
        """A method added to an existing class on dev after the merge base is
        caught."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "class Foo:\n    def existing(self):\n        pass\n",
            "initial: add Foo.existing",
        )
        _commit_file(
            repo, "tinyagentos/foo.py",
            "class Foo:\n    def existing(self):\n        pass\n\n    def new_method(self):\n        pass\n",
            "feat: add Foo.new_method",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "class Foo:\n    def existing(self):\n        pass\n",
            "refactor: remove Foo.new_method",
        )
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived = cds.check_deleted_symbols(base_tip, repo)

        assert len(violations) == 1
        assert "Foo.new_method" in violations[0].symbol

    def test_deleted_test_function_added_after_merge_base_fails(self, tmp_path: Path):
        """A test function added to dev after the merge base is caught."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tests/test_foo.py",
            "def test_existing():\n    pass\n\ndef test_new():\n    pass\n",
            "test: add test_existing and test_new",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tests/test_foo.py",
            "def test_existing():\n    pass\n",
            "refactor: remove test_new",
        )
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived = cds.check_deleted_symbols(base_tip, repo)

        assert len(violations) == 1
        assert "test_new" in violations[0].symbol

    def test_probe3_pr_deletes_long_merged_test_file_fails(self, tmp_path: Path):
        """Probe-3 shape: a PR deletes a test file that was long merged into
        dev. The merge result loses the file, so the gate must FAIL."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "initial: add function_a",
        )
        _commit_file(
            repo, "tests/test_old.py",
            "def test_old_case():\n    pass\n",
            "test: add test_old_case",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        # PR branch deletes the long-merged test file entirely.
        _checkout(repo, "pr-branch")
        _delete_file(repo, "tests/test_old.py")
        # Merge PR into main: the test file is gone from the merge result.
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived = cds.check_deleted_symbols(base_tip, repo)

        assert len(violations) == 1
        assert "test_old_case" in violations[0].symbol
        assert "test_old.py" in violations[0].symbol
        assert violations[0].added_by != "unknown"

    def test_no_signal_when_dev_unchanged_since_merge_base(self, tmp_path: Path):
        """If the PR deletes code that existed at the merge base and dev has
        not added anything since, the merge result loses that code and the
        gate now reports it as signal (inverted from the old merge-base
        subtraction behavior)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n",
            "initial: add function_a and function_b",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        # PR branch deletes function_b.
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_b",
        )
        # Merge PR into main: the merge result loses function_b.
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived = cds.check_deleted_symbols(base_tip, repo)

        assert len(violations) == 1
        assert "function_b" in violations[0].symbol

    def test_waived_via_cli_argument(self, tmp_path: Path):
        """The --waived argument also waives symbols (for manual runs)."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n",
            "initial: add function_a and function_b",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_b",
        )
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        waived_arg = {"tinyagentos/foo.py:function_b"}
        violations, waived = cds.check_deleted_symbols(base_tip, repo, waived=waived_arg)

        assert violations == []
        assert "tinyagentos/foo.py:function_b" in waived

    def test_partial_waiver_still_fails_for_unwaived(self, tmp_path: Path):
        """If two symbols are in the signal but only one is waived, the
        unwaived one still fails."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n\ndef function_c():\n    pass\n",
            "initial: add function_a, function_b, and function_c",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_b and function_c",
        )
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        pr_body = "Removes-Intentionally: tinyagentos/foo.py:function_b"
        violations, waived = cds.check_deleted_symbols(base_tip, repo, pr_body=pr_body)

        assert len(violations) == 1
        assert "function_c" in violations[0].symbol
        assert "tinyagentos/foo.py:function_b" in waived
