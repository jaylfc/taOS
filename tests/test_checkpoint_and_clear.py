"""Tests for scripts/checkpoint_and_clear.sh.

The script appends a retrospective block and a FLEET-HEALTH block to a resume
file, enforces a 32768-byte rotation limit on the artefact that ships, and only
dispatches the clear once the final on-disk size is within that limit.

The acceptance that motivates these tests: a resume file sized between
``LIMIT - block`` and ``LIMIT`` passes the pre-check but exceeds the limit once
the blocks are appended. The script must never leave the file over the limit
in that case (the old, check-before-append ordering did -- dispatching a clear
against a file that truncates on the next Read).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "checkpoint_and_clear.sh"
REPO_ROOT = Path(__file__).resolve().parent.parent

LIMIT = 32768
NEXT_MARKER = b"NEXT: rotate checkpoint\n"


def _make_resume(path: Path, size_bytes: int) -> None:
    """Write exactly `size_bytes` of resume content beginning with a NEXT marker."""
    marker = NEXT_MARKER
    assert size_bytes > len(marker), "resume too small to carry a next action"
    body = size_bytes - len(marker)
    chunks: list[bytes] = []
    remaining = body
    while remaining >= 71:  # 70 x's + newline
        chunks.append(b"x" * 70 + b"\n")
        remaining -= 71
    if remaining > 0:
        chunks.append(b"x" * remaining)
    data = marker + b"".join(chunks)
    assert len(data) == size_bytes, (len(data), size_bytes)
    path.write_bytes(data)


def _findings(tmp_path: Path) -> Path:
    p = tmp_path / "findings.txt"
    p.write_text("finding one\nfinding two\n")
    return p


def _run(resume: Path, task_id: str, findings: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "CPC_FINDINGS_FILE": str(findings)}
    return subprocess.run(
        ["bash", str(SCRIPT), str(resume), task_id],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def test_seed_at_limit_trims_and_refuses(tmp_path):
    """Passing the pre-check but exceeding the limit after append must not ship
    an oversized file: final on-disk size must be <= LIMIT and no clear dispatched."""
    resume = tmp_path / "RESUME-atlimit.md"
    _make_resume(resume, LIMIT)  # == LIMIT: pre-check passes, append overshoots
    findings = _findings(tmp_path)

    result = _run(resume, "w1:p1", findings)

    assert result.returncode != 0, result.stdout
    final = resume.stat().st_size
    assert final <= LIMIT, f"oversized checkpoint shipped: {final} > {LIMIT}"
    assert "Refusing to clear" in result.stdout
    assert "clear REQUESTED" not in result.stdout


def test_seed_just_under_limit_still_refuses_when_appends_push_over(tmp_path):
    """Seed in (LIMIT - block, LIMIT) -- here, LIMIT - 1 -- still overshoots
    after a nonzero append and must be trimmed/refused rather than cleared."""
    resume = tmp_path / "RESUME-justunder.md"
    _make_resume(resume, LIMIT - 1)
    findings = _findings(tmp_path)

    result = _run(resume, "w1:p1", findings)

    assert result.returncode != 0, result.stdout
    assert resume.stat().st_size <= LIMIT
    assert "Refusing to clear" in result.stdout
    assert "clear REQUESTED" not in result.stdout


def test_clear_dispatched_when_within_limit(tmp_path):
    """A small, in-limit checkpoint keeps its clear dispatch and stays in limit."""
    resume = tmp_path / "RESUME-small.md"
    _make_resume(resume, 500)
    findings = _findings(tmp_path)

    result = _run(resume, "w1:p2", findings)

    assert result.returncode == 0, result.stdout
    assert resume.stat().st_size <= LIMIT
    assert "clear REQUESTED for w1:p2" in result.stdout
    assert "appended 2 retrospective finding(s)" in result.stdout
    assert "Refusing to clear" not in result.stdout


def test_already_oversized_is_never_mutated(tmp_path):
    """A checkpoint already over the limit is refused without appending or
    trimming, so a human can trim it rather than the script silently cropping it."""
    resume = tmp_path / "RESUME-oversized.md"
    _make_resume(resume, 100 * 1024)
    findings = _findings(tmp_path)
    before = resume.stat().st_size

    result = _run(resume, "w1:p1", findings)

    assert result.returncode != 0, result.stdout
    assert resume.stat().st_size == before  # untouched: no append, no trim
    assert "Refusing to clear" in result.stdout
    assert "clear REQUESTED" not in result.stdout


def test_missing_resume_file_exits_nonzero(tmp_path):
    findings = _findings(tmp_path)
    result = _run(tmp_path / "does-not-exist.md", "w1:p1", findings)
    assert result.returncode != 0
    assert "resume file not found" in result.stderr
