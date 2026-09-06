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

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

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


def _api_get_routing(files: list[str], labels: list[str], changed_files: int | None = None):
    """Build a side_effect that routes files vs PR-object requests by URL.

    `changed_files` defaults to len(files) because a flat list of N paths
    corresponds to N /files records. Tests with renames override it explicitly
    since one rename record expands to two paths.
    """
    _changed = changed_files if changed_files is not None else len(files)

    def _fake(url: str, token: str | None = None, **_: object) -> list:
        if "/files" in url:
            return _files_payload(files)
        return _pr_payload(labels, changed_files=_changed)

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
            ".github/dependabot.yml",
            ".github/FUNDING.yml",
            ".github/actions/build.yml",
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
            "scripts/platform/check_foo.py",
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
            ".coderabbit.yaml",
            "docs/something.md",
            "changelog.d/foo.md",
        ],
    )
    def test_non_gate_paths_are_not_protected(self, path: str) -> None:
        assert not cgi.is_protected(path)

    def test_backslash_paths_normalised(self) -> None:
        assert cgi.is_protected(".github\\workflows\\gate.yml")
        assert not cgi.is_protected("tinyagentos\\app.py")

    def test_directory_named_check_is_not_protected(self) -> None:
        """A directory component named check_* must not match; only files
        whose basename starts with check_ are gate checkers. Regression:
        scripts/check_helpers/util.py returned True because '/check_' appeared
        in the full path (the directory name)."""
        assert not cgi.is_protected("scripts/check_helpers/util.py")
        assert cgi.is_protected("scripts/check_gate_integrity.py")


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
            if "/files" in url:
                return _files_payload(["README.md", "docs/a.md"])
            return _pr_payload([], changed_files=5)

        with patch("check_gate_integrity._api_get", side_effect=_fake):
            code, message = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_ERROR
        assert "truncated" in message

    def test_150_file_paginates_fully(self) -> None:
        # With per_page=100 the /files endpoint returns up to 100 records per
        # page; a 150-file PR needs two pages. The Link header on page 1 points
        # to page 2; the guard must follow it and collect all 150 before
        # comparing to changed_files.
        page1_files = [{"filename": f"src/file_{i:03d}.py"} for i in range(100)]
        page2_files = [{"filename": f"src/file_{i:03d}.py"} for i in range(100, 150)]
        next_url = (
            "https://api.github.com/repos/jaylfc/taOS/pulls/42/files"
            "?per_page=100&page=2"
        )
        link_hdr = f'<{next_url}>; rel="next"'
        pr_url = "https://api.github.com/repos/jaylfc/taOS/pulls/42"

        import io
        import urllib.response as _ur

        def _make_response(body: list[dict], link: str = "") -> _ur.addinfourl:
            return _ur.addinfourl(
                io.BytesIO(json.dumps(body).encode()),
                {"Link": link} if link else {},
                "https://api.github.com",
                200,
            )

        call_count = 0

        def _fake_urlopen(req, **kwargs):
            nonlocal call_count
            call_count += 1
            url = req.full_url if hasattr(req, "full_url") else req
            if url == pr_url:
                return _make_response([{
                    "labels": [],
                    "changed_files": 150,
                }])
            if "page=2" in str(url):
                return _make_response(page2_files)
            return _make_response(page1_files, link_hdr)

        with patch("check_gate_integrity.urllib.request.urlopen", side_effect=_fake_urlopen):
            code, message = cgi.check_gate_integrity("jaylfc", "taOS", 42)
        assert code == cgi.EXIT_OK, f"expected PASS, got: {message}"

    def test_rename_out_of_protected_path_fails_without_label(self) -> None:
        # A rename edits BOTH paths: renaming a workflow out of
        # .github/workflows/ disables it, yet /files reports only the new
        # filename at top level and carries the old one in
        # previous_filename. The protected old side must still be classified.
        def _fake(url: str, token: str | None = None, **_: object) -> list:
            if "/files" in url:
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
            if "/files" in url:
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
            if "/files" in url:
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
        assert any("/files" in u for u in captured)
        assert any("/pulls/42" in u for u in captured)


class TestMainTokenEnvPropagation:
    def test_main_uses_env_token_when_no_cli_token(self, monkeypatch) -> None:
        env_token = "env-cred-value"  # neutral: not a real credential shape
        monkeypatch.setenv("GITHUB_TOKEN", env_token)

        captured: dict = {}

        def fake_api_get(url, token=None, **_):
            captured["token"] = token
            if "/files" in url:
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

    def test_a_slow_git_falls_back_instead_of_crashing(self, monkeypatch) -> None:
        """`subprocess.run(..., timeout=5)` raises TimeoutExpired, which does
        NOT descend from CalledProcessError -- so it escaped the handler and
        left the script to die on an uncaught traceback.

        The direction that matters is which way it failed. An uncaught
        exception exits 1, and EXIT_BLOCKED is 1, so a merely SLOW `git` was
        indistinguishable from "this PR edits a protected gate file": an
        innocent PR gets blocked with a traceback in place of the fallback the
        docstring promises. Asserting only "it does not raise" would be one
        level too coarse -- it must land on the documented default.
        """
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        with patch(
            "check_gate_integrity.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=5),
        ):
            assert cgi._detect_repo() == ("jaylfc", "taOS")

    def test_dot_git_is_stripped_only_as_a_suffix(self, monkeypatch) -> None:
        """`.replace(".git", "")` is global, so it also ate `.git` from the
        MIDDLE of a repository name and reported a repo that does not exist.

        Inert for `jaylfc/taOS`, which is exactly why it survived: the fault
        only shows on a name carrying an embedded `.git`, and this checker is
        meant to be portable to other repos.
        """
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        fake = subprocess.CompletedProcess(
            args=["git"], returncode=0,
            stdout="https://github.com/acme/widgets.git.archive.git\n", stderr="",
        )
        with patch("check_gate_integrity.subprocess.run", return_value=fake):
            assert cgi._detect_repo() == ("acme", "widgets.git.archive")


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

    def test_workflow_subscribes_to_every_verdict_changing_activity(self) -> None:
        """The gate's verdict is a function of (base..head diff, allow label).
        It must therefore re-run on every activity that can change either, or a
        stale PASS keeps satisfying the required check on an unchanged head SHA.

        `edited` is the load-bearing one and the reason this test exists:
        retargeting a PR's base fires `edited` (with `changes.base`) and NOTHING
        else. Without it, a PR that passes against one base carries that PASS
        onto a different base whose diff may include protected gate files --
        with `dev` on loose protection (`strict: false`) the head SHA never
        moves, so nothing forces a re-run and the bypass survives.

        `labeled`/`unlabeled` are asserted alongside it because the
        `gate-integrity-allow` label flips the verdict directly; dropping
        either would make the label un-revokable in practice.
        """
        workflow = REPO_ROOT / ".github" / "workflows" / "gate-integrity.yml"
        # `on:` is parsed by PyYAML 1.1 rules as the boolean True, not "on".
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        trigger = spec.get("on", spec.get(True))
        types = set(trigger["pull_request_target"]["types"])
        required = {
            "opened",
            "synchronize",
            "reopened",
            "edited",
            "labeled",
            "unlabeled",
        }
        missing = required - types
        assert not missing, (
            f"gate-integrity.yml does not re-run on {sorted(missing)}; a verdict"
            " that can change without a re-run is a bypass"
        )

    def test_workflow_dispatch_absent_from_gate(self) -> None:
        """workflow_dispatch on the gate workflow is a spoofable required check:
        any write-access actor can dispatch with ref=<own branch> and publish a
        green check run named 'Gate integrity' against a chosen head SHA (and
        pr_number is interpolated raw into the run: block, an injection sink since
        type: number is not enforced on API dispatch). It must not be present."""
        workflow = REPO_ROOT / ".github" / "workflows" / "gate-integrity.yml"
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        trigger = spec.get("on", spec.get(True))
        trigger_keys = set(trigger.keys()) if isinstance(trigger, dict) else set()
        assert "workflow_dispatch" not in trigger_keys, (
            "gate-integrity.yml must not use workflow_dispatch (spoofable"
            " required check); re-runs use `gh run rerun` on the original"
            " event or the `labeled` re-fire"
        )

    def test_real_repo_passes_integrity(self) -> None:
        # The committed tree must be itself green: no gate script should be
        # mid-tamper. The PR files for the real repo's HEAD (none) trivially
        # pass; this guards the classify/is_protected invariants together.
        assert cgi.classify([], [], cgi.DEFAULT_ALLOW_LABEL).exit_code == cgi.EXIT_OK


# ---------------------------------------------------------------------------
# Workflow_dispatch base-ref step regression (tsk-yg3df5)
# ---------------------------------------------------------------------------


class TestWorkflowDispatchBaseRefStep:
    """The 'Resolve PR base ref' step must not silently
    fall back to the default branch when gh fails (e.g. missing GH_TOKEN).

    The defect: without GH_TOKEN, gh exits non-zero; without set -euo pipefail
    the script continues, base is empty, and checkout falls back to
    github.base_ref which is EMPTY on workflow_dispatch -> default branch.
    """

    def test_step_sets_gh_token_env(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "gate-integrity.yml"
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        step = _find_step(spec, "Resolve PR base ref")
        assert step is not None, "step not found in gate-integrity.yml"
        assert step.get("env", {}).get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}"

    def test_step_uses_set_euo_pipefail(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "gate-integrity.yml"
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        step = _find_step(spec, "Resolve PR base ref")
        run = step.get("run", "")
        assert "set -euo pipefail" in run, "step must fail closed on gh errors"

    def test_step_asserts_base_non_empty(self) -> None:
        workflow = REPO_ROOT / ".github" / "workflows" / "gate-integrity.yml"
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        step = _find_step(spec, "Resolve PR base ref")
        run = step.get("run", "")
        assert 'if [ -z "$base" ]' in run or '[[ -z "$base" ]]' in run, (
            "step must assert base is non-empty before exporting BASE_REF"
        )

    def test_gh_failure_does_not_silently_pass(self, tmp_path: Path) -> None:
        """Simulate the failure path: gh exits non-zero (no token). The step's
        actual run: block -- extracted from the YAML, not hand-copied -- must
        exit non-zero rather than emitting an empty BASE_REF."""
        workflow = REPO_ROOT / ".github" / "workflows" / "gate-integrity.yml"
        spec = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        step = _find_step(spec, "Resolve PR base ref")
        assert step is not None, "step not found in gate-integrity.yml"
        run = step.get("run", "")
        assert run, "step must have a run block"

        # Substitute GitHub Actions expressions with test values so the
        # extracted run: block is executable standalone.
        script_text = run
        script_text = script_text.replace("${{ github.repository }}", "jaylfc/taOS")
        script_text = script_text.replace(
            "${{ github.event.pull_request.number }}", "1"
        )

        fake_gh = tmp_path / "gh"
        fake_gh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_gh.chmod(0o755)

        script = tmp_path / "resolve_base_ref.sh"
        script.write_text(script_text, encoding="utf-8")
        script.chmod(0o755)

        env = {
            "PATH": str(tmp_path) + ":/usr/bin:/bin",
            "GITHUB_ENV": str(tmp_path / "github_env"),
            "HOME": str(tmp_path),
        }

        result = subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0, (
            f"step run block must fail when gh fails; stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )


def _find_step(spec: dict, name: str) -> dict | None:
    jobs = spec.get("jobs", spec.get(True, {}).get("jobs", {}))
    for job in jobs.values():
        for step in job.get("steps", []):
            if step.get("name") == name:
                return step
    return None
