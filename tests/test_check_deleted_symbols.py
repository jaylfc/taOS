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
import types
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
# _resolve_symbol isolation (in-process, no git required)
#
# _resolve_symbol mutates the interpreter's global sys.modules table. Two
# defects flow from it never restoring that table:
#   1. A synthetic parent package (with __path__ into the extracted merge
#      tree) and the reloaded module are left installed for the rest of the
#      run, so the verdict for a later symbol depends on which symbol was
#      resolved first.
#   2. A real module pre-existing at a touched key is popped and replaced
#      with a merge-tree module and never put back.
# The red assertions below fail on the buggy shape and pass once
# _resolve_symbol restores sys.modules in a finally.
# ---------------------------------------------------------------------------


class TestResolveSymbolIsolation:
    @staticmethod
    def _write_tree(tmp_path: Path, files: dict[str, str]) -> Path:
        root = tmp_path / "merge"
        for rel, content in files.items():
            full = root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        return root

    @staticmethod
    def _purge_package(name: str) -> None:
        for key in list(sys.modules):
            if key == name or key.startswith(name + "."):
                del sys.modules[key]

    def test_resolve_symbol_leaves_sys_modules_unchanged(self, tmp_path: Path):
        """_resolve_symbol must not leak into sys.modules: the key set must be
        unchanged and any entry it replaces (its module path) must keep its
        original object identity."""
        merge = self._write_tree(
            tmp_path,
            {
                "tinyagentos/__init__.py": "",
                "tinyagentos/foo.py": "def func_a():\n    pass\n",
            },
        )
        # Pre-seed a real module object at the path the function writes so the
        # identity of a pre-existing entry is asserted, not just the key set.
        sentinel = types.ModuleType("tinyagentos.foo")
        self._purge_package("tinyagentos")
        sys.modules["tinyagentos.foo"] = sentinel
        before = set(sys.modules)
        assert "tinyagentos" not in sys.modules

        try:
            cds._resolve_symbol(merge, "tinyagentos/foo.py", "func_a")
            after = set(sys.modules)
            gained = after - before
            lost = before - after
            assert not gained, f"sys.modules gained {gained}"
            assert not lost, f"sys.modules lost {lost}"
            assert (
                sys.modules.get("tinyagentos.foo") is sentinel
            ), "sys.modules entry identity changed"
        finally:
            self._purge_package("tinyagentos")

    def test_second_symbol_verdict_is_order_independent(self, tmp_path: Path):
        """Resolving symbol A before symbol B must yield the same verdict for B
        as resolving B alone in a fresh state. The merge tree keeps both a
        module file (tinyagentos/foo.py, func_a) and a same-named package
        (tinyagentos/foo/__init__.py, func_b); bar.py re-exports func_b via
        `from .foo import func_b`. Resolving the module caches tinyagentos.foo
        as the func_a-only module, which then shadows the package for a later
        bar.py resolution."""
        merge = self._write_tree(
            tmp_path,
            {
                "tinyagentos/__init__.py": "",
                "tinyagentos/foo.py": "def func_a():\n    pass\n",
                "tinyagentos/foo/__init__.py": "def func_b():\n    pass\n",
                "tinyagentos/bar.py": "from .foo import func_b\n",
            },
        )
        bar_file, bar_name = "tinyagentos/bar.py", "func_b"
        foo_file, foo_name = "tinyagentos/foo.py", "func_a"

        self._purge_package("tinyagentos")
        verdict_alone = cds._resolve_symbol(merge, bar_file, bar_name)
        assert verdict_alone is True

        self._purge_package("tinyagentos")
        try:
            cds._resolve_symbol(merge, foo_file, foo_name)
            verdict_after_a = cds._resolve_symbol(merge, bar_file, bar_name)

            assert verdict_after_a == verdict_alone
        finally:
            self._purge_package("tinyagentos")


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

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

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

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

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
        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo, pr_body=pr_body)

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

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

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

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

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

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

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

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

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

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

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
        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo, waived=waived_arg)

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
        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo, pr_body=pr_body)

        assert len(violations) == 1
        assert "function_c" in violations[0].symbol
        assert "tinyagentos/foo.py:function_b" in waived

    def test_module_shadowed_by_package_is_silent(self, tmp_path: Path):
        """A module file deleted but shadowed by a same-named package is
        SILENT: the public import path still resolves through the package."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/containers/__init__.py",
            "class ContainerInfo:\n    pass\n",
            "init: add containers package",
        )
        _commit_file(
            repo, "tinyagentos/containers.py",
            "class ContainerInfo:\n    pass\n",
            "init: add containers module (shadowed by package)",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _delete_file(repo, "tinyagentos/containers.py")
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

        assert violations == []
        assert waived == set()

    def test_reexport_dropped_from_init_fires(self, tmp_path: Path):
        """A re-export dropped from __init__.py while the def survives FIREs:
        the public name in the package namespace is gone."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo, "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n",
            "init: add function_a and function_b",
        )
        _commit_file(
            repo, "tinyagentos/__init__.py",
            "from .foo import function_b\n",
            "init: re-export function_b",
        )
        base_tip = _get_head(repo)
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo, "tinyagentos/__init__.py",
            "",
            "refactor: drop re-export",
        )
        _checkout(repo, "main")
        _git(repo, "merge", "pr-branch", "--no-edit")

        violations, waived, _ = cds.check_deleted_symbols(base_tip, repo)

        assert len(violations) == 1
        assert "function_b" in violations[0].symbol
        assert "tinyagentos/__init__.py" in violations[0].symbol
        assert violations[0].added_by != "unknown"


# ---------------------------------------------------------------------------
# --pr-head (merge-tree recomputation) path
#
# On pull_request events GitHub pins checkout HEAD to the event-time
# test-merge commit. A re-run (no new push) after the base advances compares
# the *current* base against that *stale* merge result, so symbols added to
# base after the pin are falsely reported as deleted. Passing the PR head SHA
# via --pr-head recomputes the merge result in-script against the fresh base.
# ---------------------------------------------------------------------------


def _build_stale_re_run_repo(repo: Path) -> tuple[str, str]:
    """Reproduce the stale re-run false positive.

    Produces a merge commit M (PR merged at event time), advances the base
    branch with a brand-new symbol in a file the PR never touches, then checks
    HEAD out at the stale merge commit M. Returns (base_ref, pr_head_sha) where
    base_ref resolves to the advanced base carrying the new symbol.
    """
    _commit_file(
        repo,
        "tinyagentos/foo.py",
        "def function_a():\n    pass\n",
        "init: add function_a",
    )
    _branch(repo, "pr-branch")
    _checkout(repo, "pr-branch")
    _commit_file(
        repo,
        "tinyagentos/foo.py",
        "def function_a():\n    pass\n\ndef function_pr():\n    pass\n",
        "pr: add function_pr",
    )
    pr_head = _get_head(repo)
    _checkout(repo, "main")
    _git(repo, "merge", "pr-branch", "--no-edit")
    stale_merge = _get_head(repo)
    # The base advances after the event-time merge: a new symbol lands on main
    # in a file the PR branch never touches.
    _commit_file(
        repo,
        "tests/test_stale.py",
        "def TestNewSymbol():\n    pass\n",
        "base: add TestNewSymbol after event-time merge",
    )
    # Pin HEAD at the stale merge commit, mirroring a re-run with a fresh base.
    _checkout(repo, stale_merge)
    return "main", pr_head


class TestStaleReRunFalsePositive:
    def test_head_based_lookup_on_stale_checkout_is_red(self, tmp_path: Path):
        """RED (pre-fix bug): without --pr-head the check trusts HEAD -- the
        event-time merge commit -- so a symbol added to base after the pin is
        falsely reported as deleted. This documents the exact false positive
        the --pr-head fix addresses."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        base_ref, _pr_head = _build_stale_re_run_repo(repo)

        violations, waived, conflicted = cds.check_deleted_symbols(base_ref, repo)

        assert not conflicted
        assert len(violations) == 1
        assert "TestNewSymbol" in violations[0].symbol
        assert "test_stale.py" in violations[0].symbol

    def test_pr_head_merge_tree_is_green(self, tmp_path: Path):
        """GREEN: with --pr-head the merge result is recomputed against the
        fresh base, so the post-pin symbol survives the merge and the check
        passes with exit code 0."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        base_ref, pr_head = _build_stale_re_run_repo(repo)

        violations, waived, conflicted = cds.check_deleted_symbols(
            base_ref, repo, pr_head_sha=pr_head
        )

        assert not conflicted
        assert violations == []
        assert waived == set()

    def test_pr_body_waiver_still_applies_with_pr_head(self, tmp_path: Path):
        """The Removes-Intentionally trailer still waives a named signal symbol
        when the merge result is recomputed via merge-tree."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n",
            "init: add function_a and function_b",
        )
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_b",
        )
        pr_head = _get_head(repo)
        _checkout(repo, "main")

        pr_body = "Removes-Intentionally: tinyagentos/foo.py:function_b"
        violations, waived, conflicted = cds.check_deleted_symbols(
            "main", repo, pr_body=pr_body, pr_head_sha=pr_head
        )

        assert not conflicted
        assert violations == []
        assert "tinyagentos/foo.py:function_b" in waived


class TestPrHeadControlAndConflicts:
    def test_genuine_deletion_with_pr_head_still_fails(self, tmp_path: Path):
        """CONTROL: a PR that genuinely deletes a base-added symbol is still
        caught when the merge result is recomputed via merge-tree -- the gate's
        original red case stays red on the new code path."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    pass\n\ndef function_b():\n    pass\n",
            "init: add function_a and function_b",
        )
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "refactor: remove function_b",
        )
        pr_head = _get_head(repo)
        _checkout(repo, "main")

        violations, waived, conflicted = cds.check_deleted_symbols(
            "main", repo, pr_head_sha=pr_head
        )

        assert not conflicted
        assert len(violations) == 1
        assert "function_b" in violations[0].symbol
        assert violations[0].added_by != "unknown"

    def test_conflicting_merge_tree_skips_check(self, tmp_path: Path):
        """When the recomputed merge reports conflicts the gate exits 0 with a
        note instead of failing: mergeability is gated elsewhere and a
        conflicted PR cannot silently delete symbols by merging cleanly."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "init: add function_a",
        )
        _branch(repo, "pr-branch")
        _checkout(repo, "pr-branch")
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    THEIR_CHANGE\n\ndef function_pr():\n    pass\n",
            "pr: conflicting edit",
        )
        pr_head = _get_head(repo)
        _checkout(repo, "main")
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    BASE_CHANGE\n",
            "base: conflicting edit",
        )

        violations, waived, conflicted = cds.check_deleted_symbols(
            "main", repo, pr_head_sha=pr_head
        )

        assert conflicted
        assert violations == []
        assert waived == set()

    def test_merge_tree_tool_error_is_loud_not_skipped(self, tmp_path: Path):
        """rc>1 from merge-tree (invalid ref, missing object) is a tooling
        failure, not a conflict: the gate must raise, never report
        conflicted=True and exit 0."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit_file(
            repo,
            "tinyagentos/foo.py",
            "def function_a():\n    pass\n",
            "init: add function_a",
        )

        with pytest.raises(RuntimeError, match="merge-tree"):
            cds.check_deleted_symbols(
                "main", repo, pr_head_sha="0000000000000000000000000000000000000000"
            )
