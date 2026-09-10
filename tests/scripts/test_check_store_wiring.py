"""Tests for the store-wiring defect fixed in tsk-5wcj4k."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_store_wiring.py"
SCRIPTS_DIR = _SCRIPT.parent
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_module():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("check_store_wiring", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_store_wiring"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check_mod():
    return _load_module()


def _make_diff(added_contents: list[str]) -> str:
    n = len(added_contents)
    body = "\n".join(f"+{c}" for c in added_contents)
    return (
        "diff --git a/tinyagentos/stores/foo.py b/tinyagentos/stores/foo.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tinyagentos/stores/foo.py\n"
        f"@@ -0,0 +1,{n} @@\n"
        f"{body}\n"
    )


class TestClassDefInAddedLines:
    """A + line that is merely a comment or string must NOT count as a
    class definition."""

    def test_real_class_definition_counts(self, check_mod) -> None:
        diff = _make_diff([
            "class MyStore(BaseStore):",
            "    pass",
        ])
        with patch.object(check_mod, "_get_file_at_ref", return_value=None):
            with patch.object(check_mod, "git_diff_unified", return_value=diff):
                assert check_mod._class_def_in_added_lines(
                    "tinyagentos/stores/foo.py", "MyStore", "origin/dev",
                    REPO_ROOT,
                ) is True

    def test_comment_line_does_not_count(self, check_mod) -> None:
        diff = _make_diff([
            "# class MyStore(BaseStore): replaced by OtherStore",
        ])
        with patch.object(check_mod, "_get_file_at_ref", return_value=None):
            with patch.object(check_mod, "git_diff_unified", return_value=diff):
                assert check_mod._class_def_in_added_lines(
                    "tinyagentos/stores/foo.py", "MyStore", "origin/dev",
                    REPO_ROOT,
                ) is False

    def test_string_literal_does_not_count(self, check_mod) -> None:
        diff = _make_diff([
            'LOG = "class MyStore(BaseStore): was here"',
        ])
        with patch.object(check_mod, "_get_file_at_ref", return_value=None):
            with patch.object(check_mod, "git_diff_unified", return_value=diff):
                assert check_mod._class_def_in_added_lines(
                    "tinyagentos/stores/foo.py", "MyStore", "origin/dev",
                    REPO_ROOT,
                ) is False

    def test_class_definition_with_comment_before_counts(self, check_mod) -> None:
        """A real class definition line is always detected, even if a comment
        about the same class appears on a different line."""
        diff = _make_diff([
            "# replaced by OtherStore",
            "class MyStore(BaseStore):",
            "    pass",
        ])
        with patch.object(check_mod, "_get_file_at_ref", return_value=None):
            with patch.object(check_mod, "git_diff_unified", return_value=diff):
                assert check_mod._class_def_in_added_lines(
                    "tinyagentos/stores/foo.py", "MyStore", "origin/dev",
                    REPO_ROOT,
                ) is True
