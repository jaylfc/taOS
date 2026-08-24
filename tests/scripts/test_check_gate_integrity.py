"""Tests for scripts/check_gate_integrity.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_gate_integrity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_gate_integrity", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_gate_integrity"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check_mod():
    return _load_module()


class TestIsProtectedPath:
    def test_workflow_file_is_protected(self, check_mod) -> None:
        assert check_mod._is_protected_path(".github/workflows/ci.yml")

    def test_gate_script_is_protected(self, check_mod) -> None:
        assert check_mod._is_protected_path("scripts/check_bot_review.py")

    def test_regular_file_is_not_protected(self, check_mod) -> None:
        assert not check_mod._is_protected_path("tinyagentos/app.py")

    def test_non_gate_script_is_not_protected(self, check_mod) -> None:
        assert not check_mod._is_protected_path("scripts/random_script.py")

    def test_workflow_dir_file_is_protected(self, check_mod) -> None:
        assert check_mod._is_protected_path(".github/workflows/bot-review-gate.yml")


class TestHasAllowLabel:
    def test_matching_label_present(self, check_mod) -> None:
        pr_data = {
            "labels": [{"name": "gate-integrity-allowed"}, {"name": "bug"}],
        }
        assert check_mod._has_allow_label(pr_data)

    def test_no_allow_label(self, check_mod) -> None:
        pr_data = {"labels": [{"name": "bug"}]}
        assert not check_mod._has_allow_label(pr_data)

    def test_empty_labels(self, check_mod) -> None:
        pr_data = {"labels": []}
        assert not check_mod._has_allow_label(pr_data)

    def test_case_insensitive_label(self, check_mod) -> None:
        pr_data = {"labels": [{"name": "Gate-Integrity-Allowed"}]}
        assert check_mod._has_allow_label(pr_data)


class TestCheckGateIntegrity:
    def test_no_protected_paths_touched_passes(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            [{"filename": "tinyagentos/app.py"}],
        ]):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 0
        assert "no protected paths touched" in message

    def test_protected_path_touched_without_label_fails(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            [{"filename": "scripts/check_bot_review.py"}],
        ]):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 1
        assert "FAIL" in message
        assert "gate-integrity-allowed" in message

    def test_workflow_file_touched_without_label_fails(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            [{"filename": ".github/workflows/bot-review-gate.yml"}],
        ]):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 1
        assert "FAIL" in message

    def test_protected_path_touched_with_allow_label_passes(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": [{"name": "gate-integrity-allowed"}]},
            [{"filename": "scripts/check_bot_review.py"}],
        ]):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 0
        assert "allow-label" in message

    def test_multiple_protected_paths_fails(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            [
                {"filename": "scripts/check_bot_review.py"},
                {"filename": ".github/workflows/bot-review-gate.yml"},
            ],
        ]):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 1
        assert "FAIL" in message

    def test_api_failure_on_pr_returns_error(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", return_value=None):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 2
        assert "error" in message.lower()

    def test_api_failure_on_files_returns_error(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            None,
        ]):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 2
        assert "error" in message.lower()

    def test_mixed_changes_with_protected_path_fails(self, check_mod) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            [
                {"filename": "tinyagentos/app.py"},
                {"filename": "scripts/check_store_wiring.py"},
            ],
        ]):
            exit_code, message = check_mod.check_gate_integrity("jaylfc", "taOS", 100)
        assert exit_code == 1
        assert "check_store_wiring.py" in message


class TestMain:
    def test_main_exit_1_on_protected_path_edit(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            [{"filename": "scripts/check_secret_ignores.py"}],
        ]):
            rc = check_mod.main(["100", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 1
        assert "FAIL" in captured.out

    def test_main_exit_0_on_clean_pr(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": []},
            [{"filename": "tinyagentos/app.py"}],
        ]):
            rc = check_mod.main(["100", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "PASS" in captured.out

    def test_main_exit_0_with_allow_label(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        with patch.object(check_mod, "_api_get", side_effect=[
            {"labels": [{"name": "gate-integrity-allowed"}]},
            [{"filename": "scripts/check_doc_gate.py"}],
        ]):
            rc = check_mod.main(["100", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 0
        assert "allow-label" in captured.out

    def test_main_exit_2_on_api_error(self, check_mod, capsys: pytest.CaptureFixture) -> None:
        with patch.object(check_mod, "_api_get", return_value=None):
            rc = check_mod.main(["99999", "--owner", "jaylfc", "--repo", "taOS"])
        captured = capsys.readouterr()
        assert rc == 2
        assert "error" in captured.out.lower()
