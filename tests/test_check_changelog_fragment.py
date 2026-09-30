from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "check_changelog_fragment",
    REPO_ROOT / "scripts" / "check_changelog_fragment.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MOD)

main = _MOD.main
EXIT_OK = _MOD.EXIT_OK
EXIT_VIOLATION = _MOD.EXIT_VIOLATION
EXIT_GIT_ERROR = _MOD.EXIT_GIT_ERROR
_is_test_path = _MOD._is_test_path
_check_fragment_requirement = _MOD._check_fragment_requirement


class TestIsTestPath:
    """Test the _is_test_path function matches the doc-gate behavior."""

    def test_python_test_module(self):
        assert _is_test_path("tests/test_foo.py") is True
        assert _is_test_path("tests/routes/test_bar.py") is True
        assert _is_test_path("tinyagentos/test_something.py") is True

    def test_python_non_test_module(self):
        assert _is_test_path("tests/helper.py") is False
        assert _is_test_path("tinyagentos/routes/foo.py") is False
        assert _is_test_path("scripts/check_something.py") is False

    def test_frontend_test_files(self):
        assert _is_test_path("desktop/src/components/Foo.test.tsx") is True
        assert _is_test_path("desktop/src/components/Foo.spec.ts") is True
        assert _is_test_path("desktop/src/components/__tests__/bar.test.js") is True

    def test_frontend_non_test_files(self):
        assert _is_test_path("desktop/src/components/Foo.tsx") is False
        assert _is_test_path("desktop/src/App.tsx") is False

    def test_co_located_tests(self):
        assert _is_test_path("tinyagentos/routes/__tests__/test_foo.py") is True


class TestCheckFragmentRequirement:
    """Core logic tests for the changelog fragment check."""

    def test_non_test_change_no_fragment_fails(self):
        """A non-test change under tinyagentos/ with no fragment FAILS (#2973 case)."""
        changed = [("M", "tinyagentos/routes/project_invites.py")]
        failures = _check_fragment_requirement(changed, set(), "")
        assert len(failures) == 1
        assert "project_invites.py" in failures[0]
        assert "changelog" in failures[0].lower()

    def test_non_test_change_with_fragment_passes(self):
        """Same change WITH a changelog.d fragment PASSES."""
        changed = [
            ("M", "tinyagentos/routes/project_invites.py"),
            ("A", "changelog.d/tsk-knggku-project-invites.md"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert failures == []

    def test_non_test_change_with_changelog_md_passes(self):
        """Same change WITH CHANGELOG.md edit PASSES."""
        changed = [
            ("M", "tinyagentos/routes/project_invites.py"),
            ("M", "CHANGELOG.md"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert failures == []

    def test_tests_only_change_no_fragment_passes(self):
        """A tests-only change set with no fragment PASSES (test exemption)."""
        changed = [
            ("A", "tests/test_new_feature.py"),
            ("M", "tests/routes/test_existing.py"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert failures == []

    def test_desktop_src_change_no_fragment_fails(self):
        """A non-test change under desktop/src/ with no fragment FAILS."""
        changed = [("M", "desktop/src/components/Foo.tsx")]
        failures = _check_fragment_requirement(changed, set(), "")
        assert len(failures) == 1

    def test_desktop_src_change_with_fragment_passes(self):
        """Same desktop/src change WITH fragment PASSES."""
        changed = [
            ("M", "desktop/src/components/Foo.tsx"),
            ("A", "changelog.d/2973-foo-component.md"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert failures == []

    def test_escape_hatch_pr_label_passes(self):
        """The no-changelog-needed PR label acts as escape hatch."""
        changed = [("M", "tinyagentos/routes/project_invites.py")]
        failures = _check_fragment_requirement(changed, {"no-changelog-needed"}, "")
        assert failures == []

    def test_escape_hatch_pr_body_trailer_passes(self):
        """The Changelog-Not-Needed: trailer in PR body acts as escape hatch."""
        changed = [("M", "tinyagentos/routes/project_invites.py")]
        pr_body = "Changelog-Not-Needed: internal refactor only, no user-facing change"
        failures = _check_fragment_requirement(changed, set(), pr_body)
        assert failures == []

    def test_escape_hatch_echoes_reason(self, capsys):
        """When escape hatch is used, the reason is echoed to stdout."""
        changed = [("M", "tinyagentos/routes/project_invites.py")]
        pr_body = "Changelog-Not-Needed: internal refactor only"
        failures = _check_fragment_requirement(changed, set(), pr_body)
        assert failures == []
        captured = capsys.readouterr()
        assert "internal refactor only" in captured.out


class TestMainIntegration:
    """Integration tests that run the full main() function with mocked git."""

    def _mock_git_diff(self, mock_run, changed_files):
        """Helper to mock git diff --name-status output."""
        mock_result = MagicMock()
        # Build NUL-delimited output like diff_name_status_z expects
        parts = []
        for status, path in changed_files:
            parts.append(status)
            parts.append(path)
        mock_result.stdout = "\x00".join(parts) + "\x00"
        mock_run.return_value = mock_result

    def test_main_fails_on_non_test_change_no_fragment(self):
        """Main exits non-zero for the #2973 case."""
        with patch.object(_MOD.subprocess, "run") as mock_run:
            mock_diff = MagicMock()
            mock_diff.stdout = "M\x00tinyagentos/routes/project_invites.py\x00"
            mock_run.return_value = mock_diff

            code = main(["--base", "origin/dev"])
            assert code == EXIT_VIOLATION

    def test_main_passes_with_fragment(self):
        """Main exits 0 when fragment is present."""
        with patch.object(_MOD.subprocess, "run") as mock_run:
            mock_diff = MagicMock()
            mock_diff.stdout = "M\x00tinyagentos/routes/project_invites.py\x00A\x00changelog.d/tsk-knggku-fragment.md\x00"
            mock_run.return_value = mock_diff

            code = main(["--base", "origin/dev"])
            assert code == EXIT_OK

    def test_main_passes_with_tests_only(self):
        """Main exits 0 for tests-only changes."""
        with patch.object(_MOD.subprocess, "run") as mock_run:
            mock_diff = MagicMock()
            mock_diff.stdout = "A\x00tests/test_new.py\x00"
            mock_run.return_value = mock_diff

            code = main(["--base", "origin/dev"])
            assert code == EXIT_OK

    def test_main_passes_with_pr_label_escape(self):
        """Main exits 0 when PR has no-changelog-needed label (comma string)."""
        with patch.object(_MOD.subprocess, "run") as mock_run:
            mock_diff = MagicMock()
            mock_diff.stdout = "M\x00tinyagentos/routes/project_invites.py\x00"
            mock_run.return_value = mock_diff

            code = main(["--base", "origin/dev", "--pr-labels", "no-changelog-needed"])
            assert code == EXIT_OK

    def test_main_passes_with_json_pr_label_escape_single(self):
        """Main exits 0 when single label is no-changelog-needed in CI JSON format."""
        with patch.object(_MOD.subprocess, "run") as mock_run:
            mock_diff = MagicMock()
            mock_diff.stdout = "M\x00tinyagentos/routes/project_invites.py\x00"
            mock_run.return_value = mock_diff

            labels_json = json.dumps(["no-changelog-needed"], indent=2)
            code = main(["--base", "origin/dev", "--pr-labels", labels_json])
            assert code == EXIT_OK

    def test_main_passes_with_json_pr_label_escape_multi(self):
        """Main exits 0 when no-changelog-needed is among labels in CI JSON format."""
        with patch.object(_MOD.subprocess, "run") as mock_run:
            mock_diff = MagicMock()
            mock_diff.stdout = "M\x00tinyagentos/routes/project_invites.py\x00"
            mock_run.return_value = mock_diff

            labels_json = json.dumps(["bug", "no-changelog-needed"], indent=2)
            code = main(["--base", "origin/dev", "--pr-labels", labels_json])
            assert code == EXIT_OK

    def test_main_passes_with_pr_body_trailer_escape(self):
        """Main exits 0 when PR body has Changelog-Not-Needed trailer."""
        with patch.object(_MOD.subprocess, "run") as mock_run:
            mock_diff = MagicMock()
            mock_diff.stdout = "M\x00tinyagentos/routes/project_invites.py\x00"
            mock_run.return_value = mock_diff

            code = main([
                "--base", "origin/dev",
                "--pr-body", "Changelog-Not-Needed: internal refactor"
            ])
            assert code == EXIT_OK

    def test_git_error_exits_git_error_code(self):
        """Git infrastructure failure exits with EXIT_GIT_ERROR."""
        error = subprocess.CalledProcessError(
            128, ["git", "diff"], stderr="fatal: bad revision"
        )
        with patch.object(_MOD.subprocess, "run", side_effect=error):
            code = main(["--base", "origin/no-such-ref"])
            assert code == EXIT_GIT_ERROR


class TestFragmentFilenamePatterns:
    """Test that fragment filename patterns are recognized correctly."""

    def test_pr_number_pattern(self):
        """changelog.d/<pr>-<slug>.md is recognized."""
        changed = [
            ("M", "tinyagentos/routes/foo.py"),
            ("A", "changelog.d/1234-fix-bug.md"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert failures == []

    def test_task_card_pattern(self):
        """changelog.d/tsk-<cardid>-<slug>.md is recognized."""
        changed = [
            ("M", "tinyagentos/routes/foo.py"),
            ("A", "changelog.d/tsk-abc123-fix-bug.md"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert failures == []

    def test_invalid_fragment_name_not_recognized(self):
        """Non-matching filenames in changelog.d/ do NOT count as fragments."""
        changed = [
            ("M", "tinyagentos/routes/foo.py"),
            ("A", "changelog.d/README.md"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert len(failures) == 1

    def test_changelog_d_gitkeep_not_recognized(self):
        """.gitkeep in changelog.d/ does NOT count as a fragment."""
        changed = [
            ("M", "tinyagentos/routes/foo.py"),
            ("A", "changelog.d/.gitkeep"),
        ]
        failures = _check_fragment_requirement(changed, set(), "")
        assert len(failures) == 1
