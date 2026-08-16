"""Tests for scripts/check_skip_only_tests.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

import check_skip_only_tests as checker


def test_waived_file_is_not_flagged():
    with patch.object(checker, "_git_changed", return_value=[("A", "tests/test_foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            mock_run.return_value = checker.FileResult(
                path="tests/test_foo.py", total=5, skipped=5,
            )
            violations, waived, touched, results = checker.check_skip_only_tests(
                "origin/dev",
                pr_body="Tests-Skipped-Intentionally: tests/test_foo.py, landing ahead of code",
            )
            assert len(violations) == 0
            assert "tests/test_foo.py" in waived


def test_all_skips_is_violation():
    with patch.object(checker, "_git_changed", return_value=[("A", "tests/test_foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            mock_run.return_value = checker.FileResult(
                path="tests/test_foo.py", total=5, skipped=5,
            )
            violations, _, _, _ = checker.check_skip_only_tests("origin/dev")
            assert len(violations) == 1
            assert violations[0].path == "tests/test_foo.py"


def test_partial_skips_pass():
    with patch.object(checker, "_git_changed", return_value=[("A", "tests/test_foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            mock_run.return_value = checker.FileResult(
                path="tests/test_foo.py", total=5, skipped=3, passed=2,
            )
            violations, _, _, _ = checker.check_skip_only_tests("origin/dev")
            assert len(violations) == 0


def test_deleted_file_is_ignored():
    with patch.object(checker, "_git_changed", return_value=[("D", "tests/test_foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            mock_run.return_value = checker.FileResult(path="tests/test_foo.py")
            violations, _, _, _ = checker.check_skip_only_tests("origin/dev")
            assert len(violations) == 0
            mock_run.assert_not_called()


def test_non_test_file_is_ignored():
    with patch.object(checker, "_git_changed", return_value=[("A", "tinyagentos/foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            violations, _, _, _ = checker.check_skip_only_tests("origin/dev")
            assert len(violations) == 0
            mock_run.assert_not_called()


def test_module_level_skip_is_violation(tmp_path):
    test_file = tmp_path / "test_module_skip.py"
    test_file.write_text(
        "import pytest\n" "pytest.skip('module-level skip', allow_module_level=True)\n"
    )
    result = checker._run_pytest_on_file(str(test_file), tmp_path)
    assert result.module_skipped is True
    assert "module-level skip" in result.module_skip_reason


def test_all_skips_integration(tmp_path):
    test_file = tmp_path / "test_all_skips.py"
    test_file.write_text(
        "import pytest\n"
        "\n"
        "def test_a():\n"
        "    pytest.skip('UserSharesStore not available yet')\n"
        "\n"
        "def test_b():\n"
        "    pytest.skip('depends on PR 1234')\n"
    )
    result = checker._run_pytest_on_file(str(test_file), tmp_path)
    assert result.total == 2
    assert result.skipped == 2
    assert result.passed == 0


def test_passes_integration(tmp_path):
    test_file = tmp_path / "test_passes.py"
    test_file.write_text(
        "def test_a():\n" "    assert True\n"
        "\n"
        "def test_b():\n"
        "    assert True\n"
    )
    result = checker._run_pytest_on_file(str(test_file), tmp_path)
    assert result.total == 2
    assert result.skipped == 0
    assert result.passed == 2


def test_parse_waived_files():
    assert "tests/test_foo.py" in checker._parse_waived_files(
        "Tests-Skipped-Intentionally: tests/test_foo.py, landing ahead of code"
    )
    assert "tests/test_foo.py" not in checker._parse_waived_files(
        "Tests-Skipped-Intentionally: tests/test_bar.py, different file"
    )
    assert set() == checker._parse_waived_files("no trailer here")


def test_collection_error_is_violation():
    with patch.object(checker, "_git_changed", return_value=[("A", "tests/test_foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            mock_run.return_value = checker.FileResult(
                path="tests/test_foo.py",
                total=0,
                skipped=0,
                module_skipped=False,
                pytest_exit_code=2,
            )
            violations, _, _, _ = checker.check_skip_only_tests("origin/dev")
            assert len(violations) == 1
            assert violations[0].path == "tests/test_foo.py"


def test_zero_total_not_module_skip_is_violation():
    with patch.object(checker, "_git_changed", return_value=[("A", "tests/test_foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            mock_run.return_value = checker.FileResult(
                path="tests/test_foo.py",
                total=0,
                skipped=0,
                module_skipped=False,
                pytest_exit_code=0,
            )
            violations, _, _, _ = checker.check_skip_only_tests("origin/dev")
            assert len(violations) == 1
            assert violations[0].path == "tests/test_foo.py"


def test_partial_pass_stays_clean():
    with patch.object(checker, "_git_changed", return_value=[("A", "tests/test_foo.py")]):
        with patch.object(checker, "_run_pytest_on_file") as mock_run:
            mock_run.return_value = checker.FileResult(
                path="tests/test_foo.py",
                total=3,
                skipped=1,
                passed=2,
            )
            violations, _, _, _ = checker.check_skip_only_tests("origin/dev")
            assert len(violations) == 0
