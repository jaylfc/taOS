#!/usr/bin/env python3
"""Non-discriminating test gate.

Detects guard tests that are structurally incapable of failing on the property
they name. A "guard test" is a test whose name encodes a refusal:
  _cannot_, _does_not_, _returns_403, _must_not_, _rejects_, _denies_

The common defect shape (observed in 4 real instances):
  1. The test monkeypatches the very symbol it claims to guard (taosmd #478)
  2. The test asserts a tautology (taosmd #485)
  3. The test's fixture values cannot reach the guarded comparison (taOS #2988)
  4. The test doesn't exist for half the guarded surface (taOS #2976)

This gate implements the tractable mechanical version: for each refusal-named
test, locate the function it guards and check if the test monkeypatches that
very symbol. A test that monkeypatches its own guard is a pure-AST detection
and needs no execution.

Future extension: run the test against a deliberately broken variant of the
guarded function; the test MUST fail. A test that passes against both the
correct and the broken implementation is the defect this gate reports.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Refusal patterns that identify a guard test
REFUSAL_PATTERNS = [
    "_cannot_",
    "_does_not_",
    "_returns_403",
    "_must_not_",
    "_rejects_",
    "_denies_",
]

# Patterns to extract the guarded operation from a test name
# e.g. test_release_does_not_free -> "release"
#      test_agent_cannot_claim -> "claim"
GUARDED_OP_PATTERNS = [
    (r"test_(\w+)_does_not_", 1),
    (r"test_(\w+)_cannot_", 1),
    (r"test_(\w+)_must_not_", 1),
    (r"test_(\w+)_rejects_", 1),
    (r"test_(\w+)_denies_", 1),
    (r"test_(\w+)_returns_403", 1),
    # agent/scoped variants: test_agent_cannot_claim -> "claim"
    (r"test_\w+_cannot_(\w+)", 1),
    (r"test_\w+_does_not_(\w+)", 1),
    (r"test_\w+_must_not_(\w+)", 1),
    (r"test_\w+_rejects_(\w+)", 1),
    (r"test_\w+_denies_(\w+)", 1),
]

# Route modules that are commonly guarded
ROUTE_MODULES = {
    "release": "tinyagentos.routes.a2a_gpu_lease.gpu_release",
    "claim": "tinyagentos.routes.a2a_gpu_lease.gpu_claim",
    "renew": "tinyagentos.routes.a2a_gpu_lease.gpu_renew",
    "check": "tinyagentos.routes.a2a_gpu_lease.gpu_check",
    "request": "tinyagentos.routes.a2a_gpu_lease.gpu_request",
    "authorize": "tinyagentos.agent_token_auth.check_agent_scope",
    "can_read": "tinyagentos.routes.project_notes.can_read",  # example
}


@dataclass
class GuardTest:
    """A test that encodes a refusal in its name."""
    file_path: Path
    test_name: str
    class_name: Optional[str]
    line_number: int
    guarded_op: Optional[str]  # The operation being guarded (e.g., "release")
    guarded_symbol: Optional[str]  # The full symbol path (e.g., "tinyagentos.routes.a2a_gpu_lease.gpu_release")


@dataclass
class Violation:
    """A non-discriminating test violation."""
    test: GuardTest
    reason: str
    monkeypatch_target: Optional[str] = None


def is_refusal_test(name: str) -> bool:
    """Check if a test name encodes a refusal."""
    return any(pattern in name for pattern in REFUSAL_PATTERNS)


def extract_guarded_op(test_name: str) -> Optional[str]:
    """Extract the guarded operation from a test name."""
    for pattern, group in GUARDED_OP_PATTERNS:
        match = re.search(pattern, test_name)
        if match:
            return match.group(group)
    return None


def resolve_guarded_symbol(guarded_op: str, test_file: Path) -> Optional[str]:
    """Resolve the guarded operation to a full symbol path.

    Uses heuristics based on the test file location and the operation name.
    """
    # Direct mapping for known operations
    if guarded_op in ROUTE_MODULES:
        return ROUTE_MODULES[guarded_op]

    # Try to infer from test file path
    rel_path = test_file.relative_to(TESTS_DIR)
    parts = rel_path.parts

    # tests/routes/test_routes_foo.py -> tinyagentos.routes.foo
    if len(parts) >= 2 and parts[0] == "routes":
        module_name = parts[1]
        if module_name.startswith("test_routes_"):
            route_name = module_name[len("test_routes_"):]
            if route_name.endswith(".py"):
                route_name = route_name[:-3]
            return f"tinyagentos.routes.{route_name}.gpu_{guarded_op}"

    # tests/test_foo.py -> tinyagentos.foo (or similar)
    if len(parts) >= 1:
        module_name = parts[0]
        if module_name.startswith("test_") and module_name.endswith(".py"):
            module_name = module_name[len("test_"):-3]
            return f"tinyagentos.{module_name}.{guarded_op}"

    return None


def find_monkeypatch_targets(tree: ast.AST, test_function: ast.AsyncFunctionDef | ast.FunctionDef) -> list[str]:
    """Find all monkeypatch.setattr targets in a test function.

    Returns a list of string targets like "module.function" or "module.Class.method".
    """
    targets = []

    for node in ast.walk(test_function):
        if isinstance(node, ast.Call):
            # Check for monkeypatch.setattr(...)
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "setattr":
                    # Check if it's monkeypatch.setattr
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "monkeypatch":
                        # First argument is the target
                        if node.args:
                            target = node.args[0]
                            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                                targets.append(target.value)
                            elif isinstance(target, ast.Attribute):
                                # Handle module.function style
                                parts = []
                                current = target
                                while isinstance(current, ast.Attribute):
                                    parts.append(current.attr)
                                    current = current.value
                                if isinstance(current, ast.Name):
                                    parts.append(current.id)
                                    targets.append(".".join(reversed(parts)))

    return targets


def extract_test_functions(file_path: Path) -> list[GuardTest]:
    """Extract all refusal-named test functions from a test file."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    tests = []
    current_class = None

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            current_class = node.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_refusal_test(node.name):
                guarded_op = extract_guarded_op(node.name)
                guarded_symbol = resolve_guarded_symbol(guarded_op, file_path) if guarded_op else None
                tests.append(GuardTest(
                    file_path=file_path,
                    test_name=node.name,
                    class_name=current_class,
                    line_number=node.lineno,
                    guarded_op=guarded_op,
                    guarded_symbol=guarded_symbol,
                ))
            # Reset class context after function (crude but works for flat structures)
            # Better: track class context properly with a visitor

    return tests


def check_monkeypatch_violation(test: GuardTest) -> Optional[Violation]:
    """Check if a test monkeypatches the very symbol it guards."""
    if not test.guarded_symbol:
        return None

    source = test.file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find the test function node
    test_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test.test_name:
            test_node = node
            break

    if not test_node:
        return None

    monkeypatch_targets = find_monkeypatch_targets(tree, test_node)

    # Check if any monkeypatch target matches the guarded symbol
    for target in monkeypatch_targets:
        # Normalize both for comparison
        if target == test.guarded_symbol:
            return Violation(
                test=test,
                reason=f"Test monkeypatches the very symbol it guards: {target}",
                monkeypatch_target=target,
            )
        # Also check if target ends with the guarded function name
        if target.endswith("." + test.guarded_symbol.split(".")[-1]):
            return Violation(
                test=test,
                reason=f"Test monkeypatches the guarded function ({target} -> {test.guarded_symbol})",
                monkeypatch_target=target,
            )

    return None


def scan_tests() -> list[GuardTest]:
    """Scan all test files for refusal-named tests."""
    all_tests = []

    for test_file in TESTS_DIR.rglob("test_*.py"):
        # Skip __pycache__ and similar
        if "__pycache__" in test_file.parts:
            continue
        tests = extract_test_functions(test_file)
        all_tests.extend(tests)

    return all_tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    args = parser.parse_args(argv)

    all_tests = scan_tests()

    if args.verbose:
        print(f"Found {len(all_tests)} refusal-named tests", file=sys.stderr)

    violations = []
    for test in all_tests:
        violation = check_monkeypatch_violation(test)
        if violation:
            violations.append(violation)

    if args.json:
        import json
        output = {
            "total_guard_tests": len(all_tests),
            "violations": [
                {
                    "file": str(v.test.file_path.relative_to(REPO_ROOT)),
                    "test": v.test.test_name,
                    "class": v.test.class_name,
                    "line": v.test.line_number,
                    "guarded_op": v.test.guarded_op,
                    "guarded_symbol": v.test.guarded_symbol,
                    "reason": v.reason,
                    "monkeypatch_target": v.monkeypatch_target,
                }
                for v in violations
            ],
        }
        print(json.dumps(output, indent=2))
        return 1 if violations else 0

    if violations:
        print(f"NON-DISCRIMINATING TESTS DETECTED: {len(violations)} violation(s)", file=sys.stderr)
        for v in violations:
            rel_path = v.test.file_path.relative_to(REPO_ROOT)
            class_prefix = f"{v.test.class_name}." if v.test.class_name else ""
            print(
                f"  {rel_path}:{v.test.line_number} {class_prefix}{v.test.test_name}",
                file=sys.stderr,
            )
            print(f"    Guarded operation: {v.test.guarded_op}", file=sys.stderr)
            print(f"    Guarded symbol: {v.test.guarded_symbol}", file=sys.stderr)
            print(f"    Reason: {v.reason}", file=sys.stderr)
            if v.monkeypatch_target:
                print(f"    Monkeypatch target: {v.monkeypatch_target}", file=sys.stderr)
        return 1

    print(f"OK: {len(all_tests)} guard tests scanned, no non-discriminating tests found")
    return 0


if __name__ == "__main__":
    sys.exit(main())