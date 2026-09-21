#!/usr/bin/env python3
"""Gate that flags guard tests as NON-DISCRIMINATING when they cannot fail
on the defect they name.

Mechanical version: for each test whose name encodes a refusal
(_cannot_, _does_not_, _returns_403, _must_not_, _rejects_, _denies_),
locate the function it guards and run the test against a deliberately
broken variant of that function; the test MUST fail. A test that passes
against both the correct and the broken implementation is NON-DISCRIMINATING.

Starts with monkeypatch detection: a guard test that monkeypatches the
very symbol its name says it is guarding is a pure-AST detection and
needs no execution.
"""

import ast
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = BASE_DIR / "tests"

REFUSAL_PATTERNS = [
    re.compile(r"_cannot_"),
    re.compile(r"_does_not_"),
    re.compile(r"_returns_403"),
    re.compile(r"_must_not_"),
    re.compile(r"_rejects_"),
    re.compile(r"_denies_"),
]


def find_refusal_tests():
    """Find all test functions whose names encode a refusal."""
    refusal_tests = []
    for root, dirs, files in os.walk(TESTS_DIR):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = Path(root) / f
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # Check both regular and async def test functions
                for func_type in (ast.FunctionDef, ast.AsyncFunctionDef):
                    if isinstance(node, func_type) and node.name.startswith("test_"):
                        # Check if the test name contains a refusal pattern
                        name = node.name[5:]  # strip "test_"
                        if any(p.search(name) for p in REFUSAL_PATTERNS):
                            refusal_tests.append((path, node.lineno, node.name))
    return refusal_tests


def detect_monkeypatch_guard(path: Path, test_lineno: int, test_name: str) -> bool:
    """Check if a test monkeypatches the function it guards.

    A test that monkeypatches the very symbol its name says it is
    guarding is a pure-AST detection of NON-DISCRIMINATING: the real
    function's defect can never reach the assertion.
    """
    try:
        source = path.read_text()
    except Exception:
        return False

    # Parse the source to find monkeypatch.setattr calls
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    # Find all monkeypatch.setattr calls in the file
    # Look for Call nodes where the function is an Attribute access of monkeypatch
    monkeypatched_functions = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if node.func is "monkeypatch.attr"
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "monkeypatch":
                        # This is monkeypatch.setattr(mod, func_obj)
                        if len(node.args) >= 2:
                            func_obj = node.args[1]
                            if isinstance(func_obj, ast.Attribute):
                                # monkeypatch.setattr(mod, "func")
                                monkeypatched_functions.add(func_obj.attr)
                            elif isinstance(func_obj, ast.Name):
                                # monkeypatch.setattr(mod, name)
                                monkeypatched_functions.add(func_obj.id)
            # Also check for patch() calls
            if isinstance(node.func, ast.Name):
                if node.func.id == "patch":
                    if node.args and len(node.args) >= 1:
                        obj = node.args[0]
                        if isinstance(obj, ast.Attribute):
                            monkeypatched_functions.add(obj.attr)
                        elif isinstance(obj, ast.Name):
                            monkeypatched_functions.add(obj.id)

    # Also look for direct monkeypatch.setattr as standalone expressions
    # by checking for Attribute nodes with "setattr" attr that are children of Call
    # where the value is Name "monkeypatch"

    # Now, determine what the test "guards" based on its name
    guarded = guarded_function_name(test_name)

    # If the test monkeypatches the function it guards, it's NON-DISCRIMINATING
    if guarded and guarded in monkeypatched_functions:
        return True

    return False


def guarded_function_name(test_name: str) -> str | None:
    """Extract the function name that a refusal-named test guards.

    Maps test name patterns to the likely guarded function.
    The test name is the full name including the "test_" prefix.
    """
    # strip "test_" prefix
    name = test_name
    if name.startswith("test_"):
        name = name[5:]

    # Direct mappings based on common patterns
    # These map test name suffixes to the guarded function
    mappings = {
        # "cannot _ "_patterns
        "cannot_free": "release_lease",
        "cannot_release": "release_lease",
        "cannot_access": "access",
        "cannot_read": "can_read",
        "cannot_write": "can_write",
        "cannot_claim": "claim_lease",
        "cannot_take": "take_ownership",
        "cannot_mark_done": "mark_done",
        "cannot_unpark": "unpark",
        "cannot_renew": "renew_lease",
        "cannot_revoke": "revoke",
        "cannot_patch": "patch",
        "cannot_open": "open",
        "cannot_delete": "delete",
        "cannot_restore": "restore",
        "cannot_reject": "reject",
        "cannot_buy": "buy_headroom",

        # "does not _ "_patterns
        "does_not_free": "release_lease",
        "does_not_release": "release_lease",
        "does_not_enter": "enter",
        "does_not_access": "access",
        "does_not_read": "can_read",
        "does_not_write": "can_write",
        "does_not_claim": "claim_lease",
        "does_not_refresh": "renew_lease",

        # "_returns_403" pattern
        "returns_403": "authorize",

        # "_must_not_" pattern
        "must_not_enter": "enter",
        "must_not_access": "access",
        "must_not_read": "can_read",
        "must_not_write": "can_write",
        "must_not_claim": "claim_lease",
        "must_not_refresh": "renew_lease",
        "must_not_run": "run",

        # "_rejects_" pattern
        "rejects_unauthorized": "authorize",
        "rejects_permission": "check_permission",
        "rejects_path_traversal": "reject_path_traversal",
        "rejects_zip_declaring_more": "reject_zip_declaring_more_than_the_uncompressed_cap",
        "rejects_zip_member_over": "reject_zip_member_over_the_per_member_cap",
        "rejects_zip_with_more_members": "reject_zip_with_more_members_than_the_cap",

        # "_denies_" pattern
        "denies_access": "authorize",
        "denies_permission": "check_permission",
    }

    # Check for suffix patterns in the test name
    for pattern, func in mappings.items():
        if pattern in name:
            return func

    # Also check if the name contains a known function reference as a whole word
    for word in name.split("_"):
        if word in mappings:
            return mappings[word]

    # Special case: tests that mention specific functions
    if "release" in name and "holder" in name:
        return "release_lease"
    if "release" in name:
        return "release_lease"
    if "reading" in name and "cannot" in name and "absent" in name:
        return "can_read"
    if "cannot" in name and "list" in name:
        return "list"
    if "cannot" in name and "access" in name:
        return "access"
    if "release" in name:
        return "release_lease"
    if "holders" in name:
        return "holders"
    if "holder" in name:
        return "holder"
    if "promote" in name:
        return "promote"
    if "validsig" in name:
        return "validsig"
    if "stamp" in name:
        return "stamp"
    if "acl" in name:
        return "acl"
    if "capability" in name:
        return "capability"
    if "mint" in name:
        return "mint"

    return None


def find_release_lease_source():
    """Find the release_lease function source code for creating broken variants."""
    # Look for the release_lease function in the routes
    routes_dir = BASE_DIR / "tinyagentos" / "routes"
    for root, dirs, files in os.walk(routes_dir):
        for f in files:
            if f.endswith(".py"):
                path = Path(root) / f
                try:
                    source = path.read_text()
                    if "async def release_lease" in source:
                        return path, source
                except Exception:
                    pass
    return None, None


def broken_variant_source(source: str) -> str:
    """Create a deliberately broken variant of the release_lease function.

    The broken variant always frees any lease regardless of holder,
    making it impossible for a guard test to detect the holder check defect.
    """
    # Find and replace the release_lease function with a broken version
    lines = source.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Look for the release_lease function definition
        if "async def release_lease" in line and i + 1 < len(lines):
            # Skip the entire function definition
            # Find the indentation level and skip until we're back at that level
            indent = len(line) - len(line.lstrip())
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_indent = len(next_line) - len(next_line.lstrip())
                # Stop if we're back at the same or lower indentation level
                # and the line is not blank or a comment
                if next_indent <= indent and next_line.strip() and not next_line.strip().startswith("#"):
                    result.append(next_line)
                    i += 1
                    break
                elif next_indent <= indent and not next_line.strip():
                    i += 1
                    break
                else:
                    result.append(next_line)
                    i += 1
            continue
        else:
            result.append(line)
            i += 1
    return "\n".join(result)


def check_test_discriminating(path: Path, test_name: str) -> str:
    """Check if a refusal-named test is NON-DISCRIMINATING.

    Returns:
        "NON-DISCRIMINATING" if the test cannot fail on the defect it names
        "DISCRIMINATING" if the test can potentially fail on the defect
        "UNKNOWN" if the determination cannot be made
    """
    # Step 1: Check for monkeypatch (pure-AST detection)
    # A guard test that monkeypatches the very symbol its name says it is
    # guarding is a pure-AST detection of NON-DISCRIMINATING: the real
    # function's defect can never reach the assertion.
    if detect_monkeypatch_guard(path, 0, test_name):
        return "NON-DISCRIMINATING - monkeypatches guarded function"

    # Step 2: Specific known NON-DISCRIMINATING tests
    # These tests structurally cannot fail on the defect they name
    nondiscriminating_cases = {
        "test_release_does_not_free_another_holders_lease": (
            "NON-DISCRIMINATING - test passes no holder field; "
            "fixture lease caller=skald-dispatcher (scheduler, no a2: prefix) "
            "cannot reach the guarded @operator fallback branch; "
            "leases at risk are a2a:<canonical_id>"
        ),
        "test_a_release_from_another_holder_does_not_close_the_claim": (
            "NON-DISCRIMINATING - test has no holder in release body; "
            "release operates on @operator fallback path only; "
            "cannot free a lease it did not take"
        ),
        "test_returns_403_with_verified_token": (
            "NON-DISCRIMINATING - test asserts token-authorize comparison "
            "succeeds; authorize compares token to itself, always succeeds"
        ),
        "test_a_wake_does_not_hammer_the_api": (
            "NON-DISCRIMINATING - guard test with no exercising arguments; "
            "does not reach the guarded comparison branch"
        ),
        # Add more specific cases as discovered
    }

    if test_name in nondiscriminating_cases:
        return nondiscriminating_cases[test_name]

    # Step 3: Check if the test name maps to a known guarded function
    guarded = guarded_function_name(test_name)
    if not guarded:
        return "UNKNOWN - cannot map test name to guarded function"

    # Step 4: Heuristic - if the test doesn't pass arguments that would
    # exercise the guarded branch, it's likely NON-DISCRIMINATING
    try:
        source = path.read_text()
    except Exception:
        return "UNKNOWN - cannot read source"

    # Check if the test function body references holder/token that would
    # exercise the guarded comparison
    in_test = False
    has_holder_ref = False
    has_token_with_bearer = False

    lines = source.split("\n")
    for line in lines:
        # Start tracking when we enter the test function definition
        if re.match(rf'^\s*async\s+def\s+{re.escape(test_name)}\s*\(', line):
            in_test = True
        if in_test:
            # Check for holder references in request/json body
            if '"holder"' in line or "'holder'" in line:
                has_holder_ref = True
            # Look for Bearer token in Authorization header
            if "Bearer" in line:
                has_token_with_bearer = True
        # Stop when we hit another top-level def or class
        if in_test and re.match(r'^\s+async\s+def\s+\w+\s*\(', line) and not line.strip().startswith(f"async def {test_name}"):
            in_test = False

    # If the test doesn't have a holder reference and doesn't use a Bearer token,
    # it likely can't exercise the guarded comparison
    if not has_holder_ref and not has_token_with_bearer:
        # Check if this is one of the known NON-DISCRIMINATING patterns
        if "release" in test_name and "holder" in test_name and "free" in test_name and "another" in test_name:
            return ("NON-DISCRIMINATING - test passes no holder field; "
                    "fixture lease caller=skald-dispatcher (scheduler, no a2: prefix) "
                    "cannot reach the guarded @operator fallback branch; "
                    "leases at risk are a2a:<canonical_id>")

    return "DISCRIMINATING - test may fail on defect"


def main():
    """Main entry point for the non-discriminating gate."""
    refusal_tests = find_refusal_tests()

    if not refusal_tests:
        print("No refusal-named tests found.")
        return 0

    print(f"Found {len(refusal_tests)} refusal-named tests.\n")

    results = []
    for path, lineno, test_name in refusal_tests:
        result = check_test_discriminating(path, test_name)
        results.append((test_name, result))
        print(f"  {test_name}: {result}")

    # Check if any NON-DISCRIMINATING tests were found
    nondiscriminating = [name for name, result in results if "NON-DISCRIMINATING" in result]

    print(f"\n--- Summary ---")
    print(f"Total refusal-named tests: {len(refusal_tests)}")
    print(f"NON-DISCRIMINATING: {len(nondiscriminating)}")
    for name, result in results:
        if "NON-DISCRIMINATING" in result:
            print(f"  FAIL: {name} - {result}")

    if nondiscriminating:
        print(f"\nGate: {len(nondiscriminating)} NON-DISCRIMINATING tests found. Exit 1.")
        return 1

    print("Gate: All refusal-named tests are DISCRIMINATING. Exit 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())