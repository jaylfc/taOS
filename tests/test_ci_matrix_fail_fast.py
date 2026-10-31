"""Every matrix job in our workflows must declare ``fail-fast: false``.

GitHub's default is ``fail-fast: true``: the first matrix leg to fail cancels
its siblings, and every cancelled leg contributes zero failures to the run. A
red run then lists only whichever leg died first, so the failure list read off
it is a floor, not a set. On #2547 that under-reported 345 failures in 43
modules as 42 in 11.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")])


def _fail_fast_violations(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    violations = []
    for job_id, job in (data.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        strategy = job.get("strategy")
        if not isinstance(strategy, dict) or "matrix" not in strategy:
            continue
        if strategy.get("fail-fast") is not False:
            violations.append(f"{path.name}:{job_id}")
    return violations


def test_workflows_found() -> None:
    names = {p.name for p in _workflow_files()}
    assert "ci.yml" in names, f"ci.yml not found under {WORKFLOWS_DIR}"


def test_ci_has_matrix_jobs_to_check() -> None:
    # Guards against the checker passing vacuously if the matrix shape moves.
    data = yaml.safe_load((WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8"))
    matrix_jobs = {
        job_id
        for job_id, job in data["jobs"].items()
        if isinstance(job.get("strategy"), dict) and "matrix" in job["strategy"]
    }
    assert {"shards", "test"} <= matrix_jobs, sorted(matrix_jobs)


def test_every_matrix_job_declares_fail_fast_false() -> None:
    violations = [v for p in _workflow_files() for v in _fail_fast_violations(p)]
    assert not violations, (
        "matrix jobs without `fail-fast: false` (a failing leg cancels its "
        f"siblings and hides their failures): {violations}"
    )
