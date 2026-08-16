#!/usr/bin/env python3
"""Skip-only tests guard.

Detects PRs where every test in a newly-added/modified test file skips.
Fails with the file name, skip count, and the guard that caused it so the
fix is obvious rather than a puzzle.

A ``Tests-Skipped-Intentionally: <file>, <why>`` trailer in the PR body
waives a named file, making deliberate skip-only landing a conscious act
rather than a silent one.

Usage:
    python scripts/check_skip_only_tests.py
    python scripts/check_skip_only_tests.py --base origin/dev
    python scripts/check_skip_only_tests.py --base origin/dev --pr-body "..."
"""
from __future__ import annotations

import argparse
import ast
import collections
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAILER = "Tests-Skipped-Intentionally:"


@dataclass
class FileResult:
    path: str
    total: int = 0
    passed: int = 0
    skipped: int = 0
    failed: int = 0
    error: int = 0
    skip_reasons: list[str] = field(default_factory=list)
    module_skipped: bool = False
    module_skip_reason: str = ""
    pytest_exit_code: int = 0
    defined_tests: int = 0


def _count_defined_tests(filepath: str, repo_root: Path) -> int:
    p = Path(filepath)
    abs_path = p if p.is_absolute() else repo_root / filepath
    if not abs_path.exists():
        return 0
    try:
        tree = ast.parse(abs_path.read_text())
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                count += 1
    return count


def _run_git(args: list[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _git_changed(base_ref: str, repo_root: Path) -> list[tuple[str, str]]:
    out = _run_git(["diff", "--name-status", f"{base_ref}...HEAD"], repo_root)
    changed: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        changed.append((status[0], path))
    return changed


class _Reporter:
    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.result = FileResult(path=filepath)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when != "call":
            return
        self.result.total += 1
        if report.skipped:
            self.result.skipped += 1
            reason = getattr(report, "skipped_reason", "") or ""
            self.result.skip_reasons.append(reason)
        elif report.passed:
            self.result.passed += 1
        elif report.failed:
            self.result.failed += 1
        else:
            self.result.error += 1

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if not report.skipped:
            return
        nodeid = str(report.nodeid) if report.nodeid else ""
        file_name = Path(self.filepath).name
        if nodeid == file_name or nodeid.endswith(f"/{file_name}"):
            self.result.module_skipped = True
            text = str(report.longrepr) if report.longrepr else ""
            m = re.search(r"Skipped:\s*(.+)", text, re.DOTALL)
            if m:
                self.result.module_skip_reason = m.group(1).strip().split("\n")[0]
            else:
                m = re.search(r"pytest\.(importorskip|skip)\((.+?)\)", text)
                if m:
                    self.result.module_skip_reason = (
                        f"pytest.{m.group(1)}({m.group(2)})"
                    )


def _run_pytest_on_file(filepath: str, repo_root: Path) -> FileResult:
    p = Path(filepath)
    abs_path = p if p.is_absolute() else repo_root / filepath
    if not abs_path.exists():
        return FileResult(path=filepath)

    reporter = _Reporter(filepath)
    args = [str(abs_path), "--tb=no", "-q", "-p", "no:cacheprovider"]

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = open(os.devnull, "w")  # type: ignore[assignment]
    try:
        exit_code = pytest.main(args, plugins=[reporter])
        if not isinstance(exit_code, int):
            exit_code = 1
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    reporter.result.pytest_exit_code = exit_code
    reporter.result.defined_tests = _count_defined_tests(filepath, repo_root)
    if exit_code == 5 and not reporter.result.module_skipped:
        reporter.result.total = 0
    return reporter.result


def _is_test_file(path: str) -> bool:
    return path.startswith("tests/") and path.endswith(".py")


def _parse_waived_files(pr_body: str | None) -> set[str]:
    waived: set[str] = set()
    if not pr_body:
        return waived
    for line in pr_body.splitlines():
        line = line.strip()
        if line.startswith(TRAILER):
            rest = line[len(TRAILER):].strip()
            parts = rest.split(",", 1)
            file_path = parts[0].strip()
            if file_path:
                waived.add(file_path)
    return waived


def _most_common(reasons: list[str]) -> str:
    if not reasons:
        return ""
    counter = collections.Counter(reasons)
    return counter.most_common(1)[0][0]


def check_skip_only_tests(
    base_ref: str,
    repo_root: Path = REPO_ROOT,
    pr_body: str | None = None,
) -> tuple[list[FileResult], set[str], list[str], dict[str, FileResult]]:
    changed = _git_changed(base_ref, repo_root)
    waived = _parse_waived_files(pr_body)

    violations: list[FileResult] = []
    touched_test_files: list[str] = []
    results: dict[str, FileResult] = {}

    for status, file_path in changed:
        if status.startswith("D"):
            continue
        if not _is_test_file(file_path):
            continue
        if not (
            status.startswith("A")
            or status.startswith("M")
            or status.startswith("R")
            or status.startswith("C")
        ):
            continue
        touched_test_files.append(file_path)

    for file_path in touched_test_files:
        if file_path in waived:
            print(
                f"skip-only-tests-guard: waived via "
                f"Tests-Skipped-Intentionally: {file_path}"
            )
            continue

        result = _run_pytest_on_file(file_path, repo_root)
        results[file_path] = result

        if (
            result.module_skipped
            or (result.total > 0 and result.skipped == result.total)
            or result.pytest_exit_code in (2, 3, 4)
            or (result.total == 0 and not result.module_skipped)
        ):
            violations.append(result)

    return violations, waived, touched_test_files, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None, help="Target branch ref (e.g. origin/dev)")
    parser.add_argument("--pr-body", default=None, help="PR body text")
    args = parser.parse_args(argv)

    base_ref = args.base
    if base_ref is None:
        base_ref = os.environ.get("BASE_REF", "origin/dev")

    pr_body = args.pr_body
    if pr_body is None:
        pr_body = os.environ.get("PR_BODY")

    violations, waived, touched, results = check_skip_only_tests(
        base_ref, REPO_ROOT, pr_body
    )

    for f in sorted(waived):
        print(
            f"skip-only-tests-guard: waived via "
            f"Tests-Skipped-Intentionally: {f}"
        )

    if violations:
        print(
            "SKIP-ONLY TESTS FAIL: The following PR-touched test files have tests, "
            "and ALL of them skip. This manufactures coverage that does not exist:"
        )
        for v in violations:
            if v.module_skipped:
                print(
                    f"  - {v.path}: module-level skip (0 tests collected). "
                    f"Guard: {v.module_skip_reason or 'unknown module-level skip'}"
                )
            elif v.pytest_exit_code in (2, 3, 4) or (
                v.total == 0 and not v.module_skipped
            ):
                print(
                    f"  - {v.path}: collection error "
                    f"({v.defined_tests} tests defined in file, 0 collected)"
                )
            else:
                common_reason = _most_common(v.skip_reasons)
                print(
                    f"  - {v.path}: {v.skipped}/{v.total} tests skipped. "
                    f"Guard: {common_reason or 'unknown skip reason'}"
                )
        return 1

    for file_path in touched:
        r = results.get(file_path)
        if r:
            print(
                f"skip-only-tests-guard: {file_path} -> "
                f"{r.total} collected, {r.skipped} skipped, {r.passed} passed"
            )

    print("skip-only-tests-guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
