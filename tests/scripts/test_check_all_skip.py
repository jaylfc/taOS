"""Tests for scripts/check_all_skip.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent.parent / ".github" / "scripts" / "check_all_skip.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_all_skip", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_all_skip"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check_mod():
    return _load_module()


class TestCountDefinedTests:
    def test_counts_module_level_test_functions(self, check_mod, tmp_path: Path) -> None:
        test_file = tmp_path / "test_foo.py"
        test_file.write_text(
            "def test_a():\n    assert True\n"
            "def test_b():\n    assert True\n"
        )
        assert check_mod._count_defined_tests(str(test_file)) == 2

    def test_counts_test_methods_in_test_class(self, check_mod, tmp_path: Path) -> None:
        test_file = tmp_path / "test_foo.py"
        test_file.write_text(
            "class TestSomething:\n"
            "    def test_a(self):\n"
            "        assert True\n"
            "    def test_b(self):\n"
            "        assert True\n"
        )
        assert check_mod._count_defined_tests(str(test_file)) == 2

    def test_counts_async_test_functions(self, check_mod, tmp_path: Path) -> None:
        test_file = tmp_path / "test_foo.py"
        test_file.write_text(
            "async def test_a():\n    assert True\n"
            "async def test_b():\n    assert True\n"
        )
        assert check_mod._count_defined_tests(str(test_file)) == 2

    def test_ignores_non_test_functions(self, check_mod, tmp_path: Path) -> None:
        test_file = tmp_path / "test_foo.py"
        test_file.write_text(
            "def helper():\n    pass\n"
            "def test_a():\n    assert True\n"
        )
        assert check_mod._count_defined_tests(str(test_file)) == 1

    def test_missing_file_returns_zero(self, check_mod, tmp_path: Path) -> None:
        assert check_mod._count_defined_tests(str(tmp_path / "nonexistent.py")) == 0

    def test_syntax_error_returns_zero(self, check_mod, tmp_path: Path) -> None:
        test_file = tmp_path / "test_bad.py"
        test_file.write_text("def test_a(:\n    pass\n")
        assert check_mod._count_defined_tests(str(test_file)) == 0

    def test_no_tests_returns_zero(self, check_mod, tmp_path: Path) -> None:
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("x = 1\n")
        assert check_mod._count_defined_tests(str(test_file)) == 0


class TestMainZeroCollectedWithDefinedTests:
    """0 outcomes for a file with defined tests must be a violation."""

    def test_zero_collected_with_defined_tests_is_not_clean(
        self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        test_file = tmp_path / "test_zero_collected.py"
        test_file.write_text(
            "class TestSomething:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def test_a(self):\n"
            "        assert True\n"
            "    def test_b(self):\n"
            "        assert True\n"
        )
        results = {
            str(test_file): {
                "total": 0,
                "skipped": 0,
                "passed": 0,
                "failed": 0,
                "import_guards": [],
                "defined_tests": check_mod._count_defined_tests(str(test_file)),
            }
        }
        with patch.object(check_mod, "resolve_base_ref", return_value="origin/dev"):
            with patch.object(check_mod, "get_test_outcomes", return_value=results):
                with patch.object(check_mod, "find_changed_test_files", return_value=[str(test_file)]):
                    with patch.object(check_mod, "get_pr_body", return_value=""):
                        with patch.object(check_mod.os, "environ", {"BASE_REF": "origin/dev"}):
                            rc = check_mod.main()
        captured = capsys.readouterr()
        assert rc == 1
        assert "collection yielded 0 of 2 defined tests" in captured.out

    def test_zero_collected_without_defined_tests_stays_clean(
        self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        test_file = tmp_path / "test_helper_only.py"
        test_file.write_text("x = 1\n")
        results = {
            str(test_file): {
                "total": 0,
                "skipped": 0,
                "passed": 0,
                "failed": 0,
                "import_guards": [],
                "defined_tests": 0,
            }
        }
        with patch.object(check_mod, "resolve_base_ref", return_value="origin/dev"):
            with patch.object(check_mod, "get_test_outcomes", return_value=results):
                with patch.object(check_mod, "find_changed_test_files", return_value=[str(test_file)]):
                    with patch.object(check_mod, "get_pr_body", return_value=""):
                        with patch.object(check_mod.os, "environ", {"BASE_REF": "origin/dev"}):
                            rc = check_mod.main()
        captured = capsys.readouterr()
        assert rc == 0
        assert "has 0 test outcomes, skipping check" in captured.out


    def test_zero_collected_summary_names_violation(
        self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        test_file = tmp_path / "test_zc.py"
        test_file.write_text(
            "class TestSomething:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def test_a(self):\n"
            "        assert True\n"
            "    def test_b(self):\n"
            "        assert True\n"
        )
        results = {
            str(test_file): {
                "total": 0,
                "skipped": 0,
                "passed": 0,
                "failed": 0,
                "import_guards": [],
                "defined_tests": check_mod._count_defined_tests(str(test_file)),
            }
        }
        with patch.object(check_mod, "resolve_base_ref", return_value="origin/dev"):
            with patch.object(check_mod, "get_test_outcomes", return_value=results):
                with patch.object(check_mod, "find_changed_test_files", return_value=[str(test_file)]):
                    with patch.object(check_mod, "get_pr_body", return_value=""):
                        with patch.object(check_mod.os, "environ", {"BASE_REF": "origin/dev"}):
                            rc = check_mod.main()
        captured = capsys.readouterr()
        assert rc == 1
        assert "collection yielded 0 of 2 defined tests" in captured.out
        assert "yielded no collected tests" in captured.out

    def test_waived_all_skip_not_counted_in_error_line(
        self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A WAIVED all-skip file did not fail — the ::error line must not count it."""
        zc_file = tmp_path / "test_zc.py"
        zc_file.write_text("def test_a():\n    assert True\n")
        waived_file = tmp_path / "test_waived.py"
        waived_file.write_text("def test_b():\n    assert True\n")
        results = {
            str(zc_file): {
                "total": 0, "skipped": 0, "passed": 0, "failed": 0,
                "import_guards": [],
                "defined_tests": check_mod._count_defined_tests(str(zc_file)),
            },
            str(waived_file): {
                "total": 2, "skipped": 2, "passed": 0, "failed": 0,
                "import_guards": ["pytest.importorskip"],
                "defined_tests": 2,
            },
        }
        pr_body = "Tests-Skipped-Intentionally: test_waived.py, landing ahead of code\n"
        with patch.object(check_mod, "resolve_base_ref", return_value="origin/dev"):
            with patch.object(check_mod, "get_test_outcomes", return_value=results):
                with patch.object(check_mod, "find_changed_test_files", return_value=[str(zc_file), str(waived_file)]):
                    with patch.object(check_mod, "get_pr_body", return_value=pr_body):
                        with patch.object(check_mod.os, "environ", {"BASE_REF": "origin/dev"}):
                            rc = check_mod.main()
        captured = capsys.readouterr()
        assert rc == 1
        assert "1 file(s) yielded no collected tests" in captured.out
        assert "have all tests skipping" not in captured.out


class TestMainAllSkipStillFails:
    def test_all_skips_is_violation(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        results = {
            "tests/test_foo.py": {
                "total": 3,
                "skipped": 3,
                "passed": 0,
                "failed": 0,
                "import_guards": [],
                "defined_tests": 3,
            }
        }
        with patch.object(check_mod, "resolve_base_ref", return_value="origin/dev"):
            with patch.object(check_mod, "get_test_outcomes", return_value=results):
                with patch.object(check_mod, "find_changed_test_files", return_value=["tests/test_foo.py"]):
                    with patch.object(check_mod, "get_pr_body", return_value=""):
                        with patch.object(check_mod.os, "environ", {"BASE_REF": "origin/dev"}):
                            rc = check_mod.main()
        assert rc == 1
        captured = capsys.readouterr()
        assert "all 3 of 3 tests skip" in captured.out

    def test_all_skips_waived_by_escape_hatch(
        self, check_mod, capsys: pytest.CaptureFixture
    ) -> None:
        results = {
            "tests/test_foo.py": {
                "total": 3,
                "skipped": 3,
                "passed": 0,
                "failed": 0,
                "import_guards": [],
                "defined_tests": 3,
            }
        }
        pr_body = "Tests-Skipped-Intentionally: test_foo.py, landing ahead of code\n"
        with patch.object(check_mod, "resolve_base_ref", return_value="origin/dev"):
            with patch.object(check_mod, "get_test_outcomes", return_value=results):
                with patch.object(check_mod, "find_changed_test_files", return_value=["tests/test_foo.py"]):
                    with patch.object(check_mod, "get_pr_body", return_value=pr_body):
                        with patch.object(check_mod.os, "environ", {"BASE_REF": "origin/dev"}):
                            rc = check_mod.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "WAIVED" in captured.out


class TestMainPartialSkipsPass:
    def test_partial_skips_pass(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        results = {
            "tests/test_foo.py": {
                "total": 5,
                "skipped": 2,
                "passed": 3,
                "failed": 0,
                "import_guards": [],
                "defined_tests": 5,
            }
        }
        with patch.object(check_mod, "resolve_base_ref", return_value="origin/dev"):
            with patch.object(check_mod, "get_test_outcomes", return_value=results):
                with patch.object(check_mod, "find_changed_test_files", return_value=["tests/test_foo.py"]):
                    with patch.object(check_mod, "get_pr_body", return_value=""):
                        with patch.object(check_mod.os, "environ", {"BASE_REF": "origin/dev"}):
                            rc = check_mod.main()
        assert rc == 0
        captured = capsys.readouterr()
        assert "2/5 tests skip" in captured.out
