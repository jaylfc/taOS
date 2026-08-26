"""Tests for the gate-integrity guard (scripts/check_gate_integrity.py).

CLASS DEFECT (tsk-o2vhcq, flagged CodeRabbit #2510): every `pull_request`
gate checks out the PR MERGE REF and runs its checker FROM that checkout, so a
PR can edit its own checker (or its own workflow YAML) to always-exit-0 and
green-pass the check that gates it. A lane diff touching a gate script
alongside its nominal change is exactly the shape the gates exist to catch --
and today it would go green.

`check_gate_integrity.py` runs on `pull_request_target` from the BASE ref and
inspects the PR diff via the GitHub API only (no checkout, no execution of PR
code). These tests prove the acceptance criteria:

  RED   -- a PR diff that edits a gate checker to always-exit-0 trips the guard
           (the edit to scripts/check_*.py is itself the signal; because the
           guard runs from base, the tampered checker cannot disable it).
  GREEN -- a PR touching neither .github/workflows/ nor a gate checker passes.

The API layer is mocked at the narrowest scope (check_gate_integrity._api_get)
so the decision logic is exercised end-to-end without network access. Cannot-
see is never mistaken for clean: an API failure yields EXIT_ERROR (fail
closed).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# scripts/ is not a package; make it importable like the other scripts/*.py
# gate tests (see tests/test_check_secret_ignores.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_gate_integrity as cgi  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _files_payload(filenames: list[str]) -> list[dict]:
    """Shape of GET /repos/{o}/{r}/pulls/{n}/files items: {'filename': str}."""
    return [{"filename": f} for f in filenames]


def _pr_payload(label_names: list[str], changed_files: int) -> list[dict]:
    # GET /pulls/{n} is a single object; _api_get wraps it in a list.
    return [{
        "labels": [{"name": lbl} for lbl in label_names],
        "changed_files": changed_files,
    }]


def _api_get_routing(files: list[str], labels: list[str]):
    """Build a side_effect that routes files vs PR-object requests by URL."""

    def _fake(url: str, token: str | None = None, **_: object) -> list:
        if url.endswith("/files"):
            return _files_payload(files)
        # single-object /pulls/{n} endpoint; changed_files agrees with the
        # /files listing unless a test overrides the payload deliberately.
        return _pr_payload(labels, changed_files=len(files))

    return _fake


# ---------------------------------------------------------------------------
# is_protected(path)
# ---------------------------------------------------------------------------


class TestIsProtected:
    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/bot-review-gate.yml",
            ".github/workflows/doc-gate.yml",
            ".github/workflows/secret-ignores-gate.yml",
            ".github/workflows/store-wiring-gate.yml",
            ".github/workflows/deleted-symbols-gate.yml",
            ".github/workflows/gate-integrity.yml",
            ".github/scripts/check_all_skip.py",
            "docs/doc-gate.toml",
            "pyproject.toml",
            "tests/conftest.py",
        ],
    )
    def test_gate_files_are_protected(self, path: str) -> None:
        assert cgi.is_protected(path)

    @pytest.mark.parametrize(
        "path",
        [
            "scripts/check_bot_review.py",
            "scripts/check_deleted_symbols.py",
            "scripts/check_doc_gate.py",
            "scripts/check_secret_ignores.py",
            "scripts/check_store_wiring.py",
            "scripts/check_dependency_audit_ignores.py",
            "scripts/check_manifests.py",
            "scripts/check_schema_migrations.py",
            "scripts/check_retrofit_migrations.py",
            "scripts/check_evil_merge.py",
            "scripts/check_gate_integrity.py",
        ],
    )
    def test_gate_checker_scripts_are_protected(self, path: str) -> None:
        assert cgi.is_protected(path)

    @pytest.mark.parametrize(
        "path",
        [
            "tinyagentos/app.py",
            "tinyagentos/routes/foo.py",
            "README.md",
            "desktop/package.json",
            "data/hub/identity.json",
            "scripts/audit-forks.py",
            "scripts/audit-manifests.py",
            # .github config that is not a workflow or gate script is not blocked
            ".github/dependabot.yml",
            ".github/FUNDING.yml",
            ".coderabbit.yaml",
            "docs/something.md",
            "changelog.d/foo.md",
            # a check_*.py nested in a subdir is NOT matched by the single
            # scripts/check_*.py convention the guard enforces
            "scripts/platform/check_foo.py",
        ],
    )
    def test_non_gate_paths_are_not_protected(self, path: str) -> None:
        assert not cgi.is_protected(path)

    def test_backslash_paths_normalised(self) -> None:
        assert cgi.is_protected(".github\\workflows\\gate.yml")
        assert not cgi.is_protected("tinyagentos\\app.py")


# ---------------------------------------------------------------------------
# classify(files, labels, allow_label) -- the RED / GREEN decision (pure)
# ---------------------------------------------------------------------------


class TestClassify:
    def test_green_when_no_protected_files(self) -> None:
        """GREEN control: a PR touching neither workflows nor gate scripts."""
        files = ["tinyagentos/app.py", "README.md", "desktop/src/foo.ts"]
        result = cgi.classify(files, [], cgi.DEFAULT_ALLOW_LABEL)
        assert result.exit_code == cgi.EXIT_OK
        assert "PASS" in result.message

    def test_red_when_gate_script_edited_without_label(self) -> None:
        # RED proof: a lane edits its own checker to always-exit-0. The edit to
        # scripts/check_bot_review.py is itself the signal; the base guard
        # detects it and fails the PR.
        files = [
            "tinyagentos/some_feature.py",
            "scripts/check_bot_review.py",
        ]
        result = cgi.classify(files, [], cgi.DEFAULT_ALLOW_LABEL)
        assert result.exit_code == cgi.EXIT_BLOCKED
        assert result.message.startswith("gate-integrity: FAIL")
        # the offending path is named in the message for the audit trail
        assert "scripts/check_bot_review.py" in result.message

    def test_red_when_workflow_edited_without_label(self) -> None:
        files = [".github/workflows/bot-review-gate.yml", "tinyagentos/app.py"]
        result = cgi.classify(files, [], cgi.DEFAULT_ALLOW_LABEL)
        assert result.exit_code == cgi.EXIT_BLOCKED
        assert ".github/workflows/bot-review-gate.yml" in result.message

    def test_red_when_dotgithub_scripts_gate_edited(self) -> None:
        files = [".github/scripts/check_all_skip.py"]
        result = cgi.classify(files, [], cgi.DEFAULT_ALLOW_LABEL)
        assert result.exit_code == cgi.EXIT_BLOCKED

    def test_allow_label_waives_protected_edit(self) -> None:
        files = ["scripts/check_bot_review.py"]
        result = cgi.classify(
            files, [cgi.DEFAULT_ALLOW_LABEL], cgi.DEFAULT_ALLOW_LABEL
        )
        assert result.exit_code == cgi.EXIT_OK
        assert "waived" in result.message

    def test_wrong_label_does_not_waive(self) -> None:
        files = ["scripts/check_bot_review.py"]
        # a different label name is not the allow label, so still blocked
        result = cgi.classify(files, ["some-other-label"], cgi.DEFAULT_ALLOW_LABEL)
        assert result.exit_code == cgi.EXIT_BLOCKED

    def test_multiple_protected_files_all_listed(self) -> None:
        files = [
            ".github/workflows/doc-gate.yml",
            "scripts/check_doc_gate.py",
            "scripts/check_store_wiring.py",
            "tinyagentos/app.py",
        ]
        result = cgi.classify(files, [], cgi.DEFAULT_ALLOW_LABEL)
        assert result.exit_code == cgi.EXIT_BLOCKED
        assert "scripts/check_doc_gate.py" in result.message
        assert "scripts/check_store_wiring.py" in result.message
        assert ".github/workflows/doc-gate.yml" in result.message

    def test_duplicates_collapsed_in_message(self) -> None:
        files = ["scripts/check_bot_review.py", "scripts/check_bot_review.py"]
        result = cgi.classify(files, [], cgi.DEFAULT_ALLOW_LABEL)
        assert result.exit_code == cgi.EXIT_BLOCKED
        # one protected file, even though listed twice in the diff
        assert result.message.count("scripts/check_bot_review.py") == 1


# ---------------------------------------------------------------------------
# check_gate_integrity(owner, repo, pr) -- API wiring (mocked at _api_get)
# ---------------------------------------------------------------------------


class TestCheckGateIntegrity:
    def test_red_integration_gate_script_edit_fails(self) -> None:
        # Fixture: a PR diff that edits a gate checker to always-exit-0.
        files = ["scripts/check_bot_review.py", "tinyagentos/x.py"]
        with patch(
            "check_gate_integrity._api_get",
            side_effect=_api_get_routing(files, []),
        ):
            code, message = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_BLOCKED
        assert "scripts/check_bot_review.py" in message

    def test_green_integration_clean_pr_passes(self) -> None:
        # GREEN control: a PR touching neither workflows nor gate scripts.
        files = ["tinyagentos/app.py", "README.md"]
        with patch(
            "check_gate_integrity._api_get",
            side_effect=_api_get_routing(files, []),
        ):
            code, _ = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_OK

    def test_green_integration_when_allow_label_present(self) -> None:
        files = ["scripts/check_store_wiring.py"]
        with patch(
            "check_gate_integrity._api_get",
            side_effect=_api_get_routing(files, [cgi.DEFAULT_ALLOW_LABEL]),
        ):
            code, message = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_OK
        assert "waived" in message

    def test_truncated_file_listing_fails_closed(self) -> None:
        # GitHub's /files endpoint caps at 3,000 names; beyond that it silently
        # stops. A listing shorter than the PR's own changed_files count means
        # the gate cannot see the whole diff -> EXIT_ERROR, never a pass.
        def _fake(url: str, token: str | None = None, **_: object) -> list:
            if url.endswith("/files"):
                return _files_payload(["README.md", "docs/a.md"])
            return _pr_payload([], changed_files=5)

        with patch("check_gate_integrity._api_get", side_effect=_fake):
            code, message = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_ERROR
        assert "truncated" in message

    def test_rename_out_of_protected_path_fails_without_label(self) -> None:
        # A rename edits BOTH paths: renaming a workflow out of
        # .github/workflows/ disables it, yet /files reports only the new
        # filename at top level and carries the old one in
        # previous_filename. The protected old side must still be classified.
        def _fake(url: str, token: str | None = None, **_: object) -> list:
            if url.endswith("/files"):
                return [{
                    "filename": "docs/archived-gate.yml",
                    "previous_filename": ".github/workflows/doc-gate.yml",
                    "status": "renamed",
                }]
            return _pr_payload([], changed_files=1)

        with patch("check_gate_integrity._api_get", side_effect=_fake):
            code, message = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_BLOCKED
        assert ".github/workflows/doc-gate.yml" in message

    def test_renamed_record_does_not_trip_truncation_check(self) -> None:
        # Classifying both sides of a rename must not double-count records:
        # the truncation comparison is records-vs-changed_files, so a single
        # renamed record with changed_files=1 is a complete listing, not a
        # truncated one.
        def _fake(url: str, token: str | None = None, **_: object) -> list:
            if url.endswith("/files"):
                return [{
                    "filename": "docs/b.md",
                    "previous_filename": "docs/a.md",
                    "status": "renamed",
                }]
            return _pr_payload([], changed_files=1)

        with patch("check_gate_integrity._api_get", side_effect=_fake):
            code, _ = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_OK

    def test_missing_changed_files_count_fails_closed(self) -> None:
        # Without the changed_files field truncation is undetectable; treat
        # the payload as cannot-see rather than assuming the listing is whole.
        def _fake(url: str, token: str | None = None, **_: object) -> list:
            if url.endswith("/files"):
                return _files_payload(["README.md"])
            return [{"labels": []}]

        with patch("check_gate_integrity._api_get", side_effect=_fake):
            code, _ = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_ERROR

    def test_infra_failure_fails_closed(self) -> None:
        # cannot-see must not read as clean pass: None from _api_get is an
        # infrastructure error -> EXIT_ERROR.
        with patch("check_gate_integrity._api_get", return_value=None):
            code, message = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_ERROR

    def test_fetches_files_then_labels(self) -> None:
        # The guard must consult BOTH the changed files AND the labels: a PR
        # editing a gate checker with the allow label must still pass.
        files = ["scripts/check_bot_review.py"]
        captured: list[str] = []

        def _spy(url: str, token: str | None = None, **_: object) -> list:
            captured.append(url)
            return _api_get_routing(files, [cgi.DEFAULT_ALLOW_LABEL])(url, token)

        with patch("check_gate_integrity._api_get", side_effect=_spy):
            code, _ = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_OK
        assert any(u.endswith("/files") for u in captured)
        assert any(u.endswith("/pulls/42") for u in captured)


class TestMainTokenEnvPropagation:
    def test_main_uses_env_token_when_no_cli_token(self, monkeypatch) -> None:
        env_token = "env-cred-value"  # neutral: not a real credential shape
        monkeypatch.setenv("GITHUB_TOKEN", env_token)

        captured: dict = {}

        def fake_api_get(url, token=None, **_):
            captured["token"] = token
            if url.endswith("/files"):
                return []
            return _pr_payload([], changed_files=0)

        with patch("check_gate_integrity._api_get", side_effect=fake_api_get):
            code = cgi.main(["42", "--owner", "jaylfc", "--repo", "taOS"])

        assert captured["token"] == env_token
        assert code == cgi.EXIT_OK


class TestDetectRepo:
    """SCP-style remotes lack the literal `github.com/`, so the old parse
    raised IndexError and silently fell back to the default owner/repo --
    a misattribution the caller can never see."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/acme/widgets.git",
            "git@github.com:acme/widgets.git",
            "ssh://git@github.com/acme/widgets.git",
        ],
    )
    def test_detects_owner_repo_across_remote_styles(self, url, monkeypatch) -> None:
        # GITHUB_REPOSITORY short-circuits the remote parse entirely (CI always
        # sets it); clear it so the test exercises the git-remote path.
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        fake = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=url + "\n", stderr="",
        )
        with patch("check_gate_integrity.subprocess.run", return_value=fake):
            assert cgi._detect_repo() == ("acme", "widgets")


# ---------------------------------------------------------------------------
# Live regression guards: the committed tree must not outrun the protected set
# ---------------------------------------------------------------------------


class TestCoversRealGates:
    def test_all_gate_checker_scripts_are_protected(self) -> None:
        """Every scripts/check_*.py on disk is a gate checker the guard must
        cover. If a new gate script were added outside the protected set, this
        would fail and force the set to be extended -- so the guard never goes
        silently blind to a gate."""
        scripts_dir = REPO_ROOT / "scripts"
        gate_scripts = sorted(scripts_dir.glob("check_*.py"))
        assert gate_scripts, "expected gate checker scripts under scripts/"
        for path in gate_scripts:
            rel = f"scripts/{path.name}"
            assert cgi.is_protected(rel), f"gate script not protected: {rel}"

    def test_all_workflow_files_are_protected(self) -> None:
        """Every .github/workflows/*.yml on dev is a required-check workflow
        whose edits must be caught by the base-ref guard."""
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        workflows = sorted(workflows_dir.glob("*.yml"))
        assert workflows, "expected workflow files under .github/workflows"
        for path in workflows:
            rel = f".github/workflows/{path.name}"
            assert cgi.is_protected(rel), f"workflow not protected: {rel}"

    def test_gh_scripts_gate_checker_is_protected(self) -> None:
        gh_scripts = REPO_ROOT / ".github" / "scripts"
        if not gh_scripts.is_dir():
            return
        for path in sorted(gh_scripts.glob("**/*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            assert cgi.is_protected(rel), f".github gate script not protected: {rel}"

    def test_real_repo_passes_integrity(self) -> None:
        # The committed tree must be itself green: no gate script should be
        # mid-tamper. The PR files for the real repo's HEAD (none) trivially
        # pass; this guards the classify/is_protected invariants together.
        assert cgi.classify([], [], cgi.DEFAULT_ALLOW_LABEL).exit_code == cgi.EXIT_OK
