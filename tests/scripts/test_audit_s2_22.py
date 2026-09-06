"""RED test for S2-22: un-hashed curl | sh / wget | sh in catalog install paths."""

from __future__ import annotations

import re
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent

# app-catalog is at the project root, two levels up from tests/scripts/
APP_CATALOG = TEST_DIR.parent.parent / "app-catalog"

UNSAFE_CURL_PATTERN = re.compile(r"curl\s+[-fsSL]{2,}\s+https?://\S+\s*\|\s*(?:sh|bash)")
UNSAFE_WGET_PATTERN = re.compile(r"wget\s+[-qO]{1,2}\s*-\s*\|\s*(?:sh|bash)")


def _has_sha256_check(content: str) -> bool:
    """Check if file content has a sha256sum verification of a downloaded file."""
    if re.search(r"sha256sum\s+[-c]\s*", content):
        return True
    if re.search(r'echo\s+["\'].*sha256.*\|\s*sha256sum\s+[-c]\s*', content):
        return True
    return False


def _check_file_for_unsafe_patterns(filepath: Path) -> list[dict]:
    """Check a file for unsafe curl|wget | sh patterns without sha256 verification."""
    results: list[dict] = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return results

    for i, line in enumerate(content.splitlines(), start=1):
        if UNSAFE_CURL_PATTERN.search(line):
            has_check = _has_sha256_check(content)
            if not has_check:
                results.append(
                    {
                        "line": i,
                        "pattern": "curl | sh without sha256 check",
                        "context": line.strip(),
                        "file": str(filepath),
                    }
                )

        if UNSAFE_WGET_PATTERN.search(line):
            has_check = _has_sha256_check(content)
            if not has_check:
                results.append(
                    {
                        "line": i,
                        "pattern": "wget | sh without sha256 check",
                        "context": line.strip(),
                        "file": str(filepath),
                    }
                )

    return results


def test_no_unsafe_curl_sh_without_sha256():
    """FAIL if any curl | sh or wget | sh found without sha256 verification.

    This is the RED test: it should fail on origin/dev before the fix,
    and pass after the fix.
    Acceptance: greps app-catalog/**/{Dockerfile,*.sh} for 'curl ... | sh' /
    'wget ... | sh' without a preceding sha256sum check and fails on the three
    sites; passes after the change.
    """
    all_matches: list[dict] = []
    for filepath in sorted(APP_CATALOG.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.name != "Dockerfile" and filepath.suffix != ".sh":
            continue
        matches = _check_file_for_unsafe_patterns(filepath)
        all_matches.extend(matches)

    assert len(all_matches) == 0, (
        f"Found {len(all_matches)} unsafe curl|wget | sh patterns without sha256 check:\n"
        + "\n".join(
            f"  {m['file']}:{m['line']} - {m['pattern']}: {m['context']}"
            for m in all_matches
        )
    )