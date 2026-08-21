#!/usr/bin/env python3
"""CI check: fail a PR whose new tests ALL SKIP (green that asserts nothing).

Scans test files the PR adds/modifies and fails if every test in a file
is skipped (via pytest.importorskip or pytest.skip).  An escape hatch
trailer in the PR body waives the check.
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def resolve_base_ref(base_ref: str) -> str:
    """Resolve base_ref to a revision that exists in this checkout.

    In Actions, github.event.pull_request.base.ref is a bare branch name
    ("dev"); the runner checkout has it only as origin/dev.
    """
    for candidate in (base_ref, f"origin/{base_ref}"):
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return candidate
    print(f"::error::base ref {base_ref!r} not found (tried {base_ref!r} and 'origin/{base_ref}')")
    sys.exit(1)


def find_changed_test_files(base_ref: str) -> list[str]:
    """Return test files (test_*.py) changed between base_ref and HEAD."""
    # git diff --name-only <base>..HEAD; exclude deleted files — pytest on a
    # missing path exits 4 and would fail the gate on any test-file deletion
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", f"{base_ref}..HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"::error::git diff failed: {result.stderr}")
        sys.exit(1)

    all_changed = result.stdout.strip().splitlines()
    test_files = [f for f in all_changed if os.path.basename(f).startswith("test_") and f.endswith(".py")]
    return test_files


def get_test_outcomes(test_files: list[str]) -> dict[str, dict]:
    """Run pytest -rs on each test file and return skip/pass/fail counts.

    Returns: {filename: {"total": int, "skipped": int, "passed": int, "failed": int,
                       "import_guards": [str]}}
    """
    results: dict[str, dict] = {}

    for filepath in test_files:
        # Run pytest on just this file with -rs (short summary + result)
        # Also use --tb=no to truncate tracebacks, -q for quiet
        cmd = [
            "uv", "run", "--no-sync",
            "pytest", filepath, "-rs", "--tb=no", "-q",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)

        # rc 0=pass, 1=test failures, 5=no tests collected: all parseable.
        # Anything else (2=interrupted/collection error, 3=internal, 4=usage)
        # means the outcome counts below would be garbage — fail loudly
        # instead of letting a broken file sail through as "0 outcomes".
        if proc.returncode not in (0, 1, 5):
            print(
                f"::error::pytest exited {proc.returncode} on {filepath} "
                f"(collection error or crash) — cannot judge skip status.\n"
                f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}"
            )
            sys.exit(1)

        # Parse stdout for summary lines like "4 passed, 2 skipped, 1 failed"
        # and individual test outcomes like "test_name SKIPPED"
        output = proc.stdout + proc.stderr

        total = 0
        skipped = 0
        passed = 0
        failed = 0
        import_guards: list[str] = []

        # Count from summary line: "X passed, Y skipped, Z failed"
        m = re.search(r"(\d+)\s+passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+)\s+skipped", output)
        if m:
            skipped = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            failed = int(m.group(1))
        total = passed + skipped + failed

        # Also count from individual test outcome lines: "test_name SKIPPED"
        # Pattern: word characters, dash, underscore, followed by SKIPPED/FAILED/PASSED
        outcome_lines = re.findall(
            r"^([\w\.-]+)\s+(SKIPPED|FAILED|PASSED)\s*$",
            output,
            re.MULTILINE,
        )
        # Always parse guards from the test file, independent of outcome line matching
        file_guards = _parse_guards_from_file(filepath)
        import_guards.extend(file_guards)

        for _name, outcome in outcome_lines:
            total += 1
            if outcome == "SKIPPED":
                skipped += 1
            elif outcome == "FAILED":
                failed += 1
            elif outcome == "PASSED":
                passed += 1

        # If we couldn't parse total from summary, use outcome lines
        if total == 0:
            total = len(outcome_lines)
            skipped = sum(1 for _o, o in outcome_lines if o == "SKIPPED")
            passed = sum(1 for _o, o in outcome_lines if o == "PASSED")
            failed = sum(1 for _o, o in outcome_lines if o == "FAILED")

        results[filepath] = {
            "total": total,
            "skipped": skipped,
            "passed": passed,
            "failed": failed,
            "import_guards": import_guards,
            "defined_tests": _count_defined_tests(filepath),
        }

    return results


def _parse_guards_from_file(filepath: str) -> list[str]:
    """Parse a test file to find importorskip targets and pytest.skip reasons.

    Returns list of guard strings like 'importorskip: module_name' or 'skip: reason'.
    """
    guards: list[str] = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return guards

    for match in re.finditer(r"importorskip\(['\"]([^'\"]+)['\"]\)", content):
        guards.append(f"importorskip:{match.group(1)}")

    for match in re.finditer(r"pytest\.skip\(['\"]([^'\"]*)['\"]\)", content):
        reason = match.group(1).strip()
        guards.append(f"skip:{reason}")

    return guards


def _count_defined_tests(filepath: str) -> int:
    """Count def test_* functions defined in a file using AST."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return 0
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                count += 1
    return count


def get_pr_body() -> str:
    """Return the PR body from GitHub context."""
    # GITHUB_EVENT_PATH is set in the GitHub Actions environment
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        # Fallback: try to read from local file for testing
        event_path = "/tmp/github_event.json"
        if os.path.exists(event_path):
            with open(event_path, "r") as f:
                event = json.load(f)
                return event.get("pull_request", {}).get("body", "")

    try:
        with open(event_path, "r", encoding="utf-8") as f:
            event = json.load(f)
        return event.get("pull_request", {}).get("body", "")
    except Exception:
        return ""


def has_escape_hatch(pr_body: str, filepath: str) -> bool:
    """Check if the PR body has a Tests-Skipped-Intentionally trailer for this file.

    Expected format: Tests-Skipped-Intentionally: <file>, <why>
    The file path must match (basename match is sufficient).
    """
    # Look for the trailer pattern at the end of the PR body or on its own line
    # Pattern: "Tests-Skipped-Intentionally: <file>, <why>"
    basename = os.path.basename(filepath)

    # Search for the trailer in the PR body
    lines = pr_body.splitlines()
    # Check last few lines for the trailer
    for line in lines:
        stripped = line.strip()
        m = re.match(r"Tests-Skipped-Intentionally:\s*(.+)", stripped)
        if m:
            trailer_claim = m.group(1).strip()
            # "<file>, <why>": exact basename match plus a non-empty reason,
            # so a waiver for test_x.py.bak cannot waive test_x.py
            claimed_file, _, reason = trailer_claim.partition(",")
            if claimed_file.strip() == basename and reason.strip():
                return True
    return False


def main() -> int:
    base_ref = os.environ.get("BASE_REF", "")
    if not base_ref:
        # Try to detect from git
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
        head_ref = result.stdout.strip()
        # For PRs, the base is typically the branch name from the event
        # Try common patterns
        result2 = subprocess.run(
            ["git", "log", "--format=%D", "-1"],
            capture_output=True, text=True,
        )
        print(f"HEAD: {head_ref}, REMOTE: {result2.stdout}")
        # Default to origin/dev if we can't determine
        base_ref = "origin/dev"

    base_ref = resolve_base_ref(base_ref)
    print(f"Using base reference: {base_ref}")

    # Find changed test files
    test_files = find_changed_test_files(base_ref)
    print(f"Changed test files: {test_files}")

    if not test_files:
        print("No test files changed — nothing to check.")
        return 0

    # Get test outcomes
    results = get_test_outcomes(test_files)

    # Get PR body for escape hatch
    pr_body = get_pr_body()
    print(f"PR body length: {len(pr_body)} chars")

    any_fail = False

    for filepath, info in results.items():
        skip_count = info["skipped"]
        total = info["total"]
        defined_tests = info["defined_tests"]
        guards = info["import_guards"]

        if total == 0:
            if defined_tests > 0:
                print(
                    f"FAIL: {filepath} — collection yielded 0 of "
                    f"{defined_tests} defined tests"
                )
                any_fail = True
            else:
                print(f"WARNING: {filepath} has 0 test outcomes, skipping check")
            continue

        if skip_count == total:
            # All tests skip — check for escape hatch
            if has_escape_hatch(pr_body, filepath):
                print(
                    f"WAIVED: {filepath} — all {skip_count} tests skip, "
                    f"escape hatch present in PR body. Guards: {guards}"
                )
            else:
                print(
                    f"FAIL: {filepath} — all {skip_count} of {total} tests skip "
                    f"(guards: {', '.join(guards) or 'none detected'}). "
                    f"This PR would manufacture coverage with no real tests."
                )
                any_fail = True
        else:
            # Only some skip — v1 scope: we only fail on ALL skip
            print(
                f"OK: {filepath} — {skip_count}/{total} tests skip (partial, v1 scope). "
                f"Guards: {', '.join(guards) or 'none detected'}"
            )

    # Report summary in PR check output
    total_changed = len(test_files)
    all_skip_files = sum(
        1 for info in results.values() if info["total"] > 0 and info["skipped"] == info["total"]
    )
    zero_collected_files = sum(
        1 for info in results.values() if info["total"] == 0 and info["defined_tests"] > 0
    )
    # The error line must mirror exactly the conditions that set any_fail:
    # a WAIVED all-skip file did not fail, so it must not be counted here
    # (all_skip_files keeps including waived files for the OK-branch note).
    unwaived_all_skip = sum(
        1 for filepath, info in results.items()
        if info["total"] > 0
        and info["skipped"] == info["total"]
        and not has_escape_hatch(pr_body, filepath)
    )

    if any_fail:
        parts = []
        if unwaived_all_skip > 0:
            parts.append(f"{unwaived_all_skip} file(s) have all tests skipping")
        if zero_collected_files > 0:
            parts.append(f"{zero_collected_files} file(s) yielded no collected tests")
        print(f"\n::error:: {', '.join(parts)} — see above for details")
        return 1

    print(f"\nOK: {total_changed} test file(s) checked, no all-skip violations")
    if all_skip_files > 0:
        print(f"  ({all_skip_files} file(s) have all tests skip but waived via escape hatch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())