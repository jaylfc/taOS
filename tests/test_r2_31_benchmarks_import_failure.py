"""Test that the benchmarks/ directory is removed and no stale references remain.

R2-31 (card tsk-sphbbu): benchmarks/ was deleted because every script imports
tinyagentos.* modules that do not exist.  This test asserts the removal is
complete -- the directory must not exist and no references to it should remain
in README.md, docs/, .github/, scripts/, or tinyagentos/ (excluding changelog.d/
and docs/audit/).  References to the /api/benchmarks/ API endpoint are excluded
as legitimate URL routes, not directory references.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"


class TestR231BenchmarksRemoved:
    """Assert benchmarks/ directory and all references are removed."""

    def test_benchmarks_directory_does_not_exist(self) -> None:
        assert not BENCHMARKS_DIR.exists(), (
            f"benchmarks/ directory still exists at {BENCHMARKS_DIR}"
        )

    def test_no_stale_benchmarks_references(self) -> None:
        result = subprocess.run(
            ["grep", "-rnE",
             "--exclude-dir=changelog.d",
             "--exclude-dir=audit",
             r"(^|[^A-Za-z0-9_./-])benchmarks/",
             "README.md", "docs/", ".github/", "scripts/", "tinyagentos/"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode in (0, 1), (
            f"grep exited {result.returncode} (expected 0 or 1); stderr:\n"
            f"{result.stderr}"
        )
        lines = [l for l in result.stdout.strip().split("\n") if l]
        stale = [l for l in lines if "/api/benchmarks" not in l]
        assert not stale, (
            "Stale benchmarks/ directory references found:\n" + "\n".join(stale)
        )
