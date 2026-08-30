"""Tests for the workflow_dispatch base-ref resolve step in gate-integrity.yml.

CLASS DEFECT fix-forward (fix-forward #2621): the "Resolve PR base ref for
workflow_dispatch" step called `gh api` without GH_TOKEN, so `gh` failed, the
empty command substitution was written to GITHUB_ENV as BASE_REF="", and with
no `set -euo pipefail` the step exited 0. The downstream checkout then used
`github.base_ref` (empty on workflow_dispatch) and silently checked out the
default branch instead of the PR's actual base.

These tests prove the FIX by executing the step's `run` script directly
against a fake `gh` binary on PATH:

  * Structural -- the committed step sets GH_TOKEN, runs under
    `set -euo pipefail`, and asserts the resolved base ref is non-empty before
    writing BASE_REF.
  * Happy path -- with a token and a working `gh`, BASE_REF is written
    correctly. (A guard that CANNOT fail on this defect -- the defect lives on
    the failure path.)
  * Failure path (MUST MUTATE) -- with the token blanked or `gh api` forced
    to fail, the step FAILS (non-zero exit) instead of silently writing an
    empty BASE_REF and exiting 0, which is what let the wrong-branch checkout
    slip through unobserved.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate-integrity.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _resolve_step(spec: dict) -> dict:
    """Find the 'Resolve PR base ref for workflow_dispatch' step in the workflow."""
    for step in spec["jobs"]["gate-integrity"]["steps"]:
        if step.get("name") == "Resolve PR base ref for workflow_dispatch":
            return step
    pytest.fail("Resolve PR base ref step not found in gate-integrity.yml")


def _step_run_substituted() -> str:
    """Extract the step's run script with GHA expressions replaced by test values."""
    run = _resolve_step(_load_workflow())["run"]
    run = run.replace("${{ github.repository }}", "jaylfc/taOS")
    run = run.replace("${{ github.event.inputs.pr_number }}", "42")
    return run


def _write_fake_gh(bin_dir: Path, *, mode: str) -> Path:
    """Write a fake `gh` binary into bin_dir.

    mode controls behaviour:
      'ok'    - require GH_TOKEN; print 'dev' (mimics gh api ... --jq .base.ref)
      'fail'  - always exit 1 (simulates gh auth/API failure)
      'empty' - exit 0 with no output (simulates an empty base.ref)
    """
    scripts = {
        "ok": (
            "#!/usr/bin/env bash\n"
            'if [ -z "${GH_TOKEN:-}" ]; then\n'
            '  echo "gh: To use GitHub CLI in a GitHub Actions workflow, '
            'set the GH_TOKEN environment variable" >&2\n'
            "  exit 1\n"
            "fi\n"
            "echo dev\n"
        ),
        "fail": (
            "#!/usr/bin/env bash\n"
            'echo "gh: To use GitHub CLI in a GitHub Actions workflow, '
            'set the GH_TOKEN environment variable" >&2\n'
            "exit 1\n"
        ),
        "empty": ("#!/usr/bin/env bash\nexit 0\n"),
    }
    gh_path = bin_dir / "gh"
    gh_path.write_text(scripts[mode])
    gh_path.chmod(0o755)
    return gh_path


def _run_step(
    tmp_path: Path,
    *,
    gh_mode: str = "ok",
    set_token: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute the step's run script as a bash subprocess.

    A fake `gh` is placed first on PATH so no real GitHub CLI is needed.
    GITHUB_ENV is redirected to a temp file for inspection.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_gh(bin_dir, mode=gh_mode)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_ENV"] = str(tmp_path / "env")
    if set_token:
        env["GH_TOKEN"] = "fake-token"
    else:
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)

    return subprocess.run(
        ["bash", "-c", _step_run_substituted()],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _env_contents(tmp_path: Path) -> str:
    env_file = tmp_path / "env"
    if not env_file.exists():
        return ""
    return env_file.read_text()


# ---------------------------------------------------------------------------
# Structural tests: the committed YAML must carry the fix
# ---------------------------------------------------------------------------


class TestResolveStepStructure:
    def test_step_sets_gh_token_env(self) -> None:
        step = _resolve_step(_load_workflow())
        assert step.get("env", {}).get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}"

    def test_step_uses_set_euo_pipefail(self) -> None:
        run = _resolve_step(_load_workflow())["run"]
        assert "set -euo pipefail" in run

    def test_step_asserts_base_non_empty(self) -> None:
        """An empty base ref must be rejected before BASE_REF is written,
        so a silent empty substitution cannot propagate to checkout."""
        run = _resolve_step(_load_workflow())["run"]
        assert "-z" in run and "$base" in run
        # The rejection path must exit non-zero.
        assert "exit 1" in run


# ---------------------------------------------------------------------------
# Happy path: token present + gh works => BASE_REF written (guard that
# CANNOT fail on this defect -- the defect lives on the failure path)
# ---------------------------------------------------------------------------


class TestResolveBaseRefHappyPath:
    def test_base_ref_written_to_github_env(self, tmp_path: Path) -> None:
        result = _run_step(tmp_path, gh_mode="ok", set_token=True)
        assert result.returncode == 0, result.stderr
        assert "BASE_REF=dev" in _env_contents(tmp_path)


# ---------------------------------------------------------------------------
# Failure path (MUST MUTATE): the step must FAIL rather than silently
# writing an empty BASE_REF and exiting 0, which is what let checkout fall
# back to the default branch.
# ---------------------------------------------------------------------------


class TestResolveBaseRefFailurePath:
    def test_blanking_token_causes_step_to_fail(self, tmp_path: Path) -> None:
        """Mutation: remove GH_TOKEN (as the buggy version did).  gh fails and
        the step must exit non-zero -- it must NOT write BASE_REF and exit 0."""
        result = _run_step(tmp_path, gh_mode="ok", set_token=False)
        assert result.returncode != 0, result.stderr
        assert "BASE_REF=" not in _env_contents(tmp_path)

    def test_forced_gh_failure_causes_step_to_fail(self, tmp_path: Path) -> None:
        """Mutation: GH_TOKEN is present but gh api still fails outright.
        `set -euo pipefail` must turn that into a non-zero step exit."""
        result = _run_step(tmp_path, gh_mode="fail", set_token=True)
        assert result.returncode != 0, result.stderr
        assert "BASE_REF=" not in _env_contents(tmp_path)

    def test_empty_base_ref_assertion_causes_step_to_fail(self, tmp_path: Path) -> None:
        """gh exits 0 but yields an empty base ref. The assertion must catch
        this independent of set -e -- no BASE_REF is written."""
        result = _run_step(tmp_path, gh_mode="empty", set_token=True)
        assert result.returncode != 0, result.stderr
        assert "::error::" in result.stderr
        assert "BASE_REF=" not in _env_contents(tmp_path)

    def test_step_does_not_silently_exit_zero(self, tmp_path: Path) -> None:
        """The core regression: with no token, the old script exited 0 while
        writing BASE_REF="" -- checkout then used github.base_ref (empty on
        workflow_dispatch) and checked out the default branch. The fixed step
        must NOT exit 0 under this mutation."""
        result = _run_step(tmp_path, gh_mode="ok", set_token=False)
        assert result.returncode != 0
        # No BASE_REF (real or empty) must reach GITHUB_ENV on the failure path.
        contents = _env_contents(tmp_path)
        assert not contents or "BASE_REF" not in contents
