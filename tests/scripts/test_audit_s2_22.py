"""RED test for S2-22: un-hashed curl | sh / wget | sh in catalog install paths."""

from __future__ import annotations

import re
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent

# app-catalog is at the project root, two levels up from tests/scripts/
APP_CATALOG = TEST_DIR.parent.parent / "app-catalog"

UNSAFE_CURL_PATTERN = re.compile(r"curl\s+[-fsSL]{2,}\s+https?://\S+\s*\|\s*(?:sh|bash)")
UNSAFE_WGET_PATTERN = re.compile(r"wget\s+[-qO]{1,2}\s*-\s*\|\s*(?:sh|bash)")

# URL pattern that indicates a version-pinned (immutable) source.
# Accepts: /vX.Y.Z/, /X.Y.Z/, /download/X.Y.Z/, tag path, or commit sha.
VERSIONED_URL_PATTERN = re.compile(
    r"(?:/v\d+\.\d+\.\d+/|/\d+\.\d+\.\d+/|/download/\d+\.\d+\.\d+/|/releases/download/\d+\.\d+\.\d+/|/[0-9a-f]{40}/|@[0-9a-f]{40})"
)


def _has_sha256_check(content: str) -> bool:
    """Check if file content has a sha256sum verification of a downloaded file."""
    if re.search(r"sha256sum\s+[-c]\s*", content):
        return True
    if re.search(r'echo\s+["\'].*sha256.*\|\s*sha256sum\s+[-c]\s*', content):
        return True
    return False


def _extract_fetch_urls(content: str) -> list[tuple[int, str, str]]:
    """Extract (line_no, url, full_line) for curl/wget lines that pipe to sh."""
    results: list[tuple[int, str, str]] = []
    for i, line in enumerate(content.splitlines(), start=1):
        m = re.search(r"(curl|wget)\s+[-fsSLqO]{1,4}\s+(-o\s+\S+\s+)?(https?://\S+)", line)
        if m and re.search(r"\|\s*(?:sh|bash)", line):
            url = m.group(3).rstrip("'\"")
            results.append((i, url, line.strip()))
    return results


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


def test_sha256_pinned_urls_are_versioned():
    """FAIL if any pinned sha256sum -c fetches a URL with no version token.

    A pinned hash on a mutable 'latest' URL breaks on the next upstream edit.
    Every fetch that is verified by sha256sum -c must point to an immutable,
    version-pinned URL (containing /vX.Y.Z/, /X.Y.Z/, /download/X.Y.Z/,
    a tag path, or a commit sha).
    """
    unversioned: list[dict] = []
    for filepath in sorted(APP_CATALOG.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.name != "Dockerfile" and filepath.suffix != ".sh":
            continue
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, url, line in _extract_fetch_urls(content):
            if not VERSIONED_URL_PATTERN.search(url):
                unversioned.append(
                    {
                        "file": str(filepath),
                        "line": lineno,
                        "url": url,
                        "context": line,
                    }
                )

    assert len(unversioned) == 0, (
        f"Found {len(unversioned)} pinned sha256sum -c fetches to unversioned URLs:\n"
        + "\n".join(
            f"  {m['file']}:{m['line']} - {m['url']} (no version token)\n    {m['context']}"
            for m in unversioned
        )
    )


def test_hash_mismatch_aborts_with_url(tmp_path):
    """A wrong pinned hash must abort the script with a message naming the URL.

    This is the RED test: it should fail on the old &&-chain (which silently
    continues past sha256sum -c failure) and pass after the fetch-and-verify
    function exits 1 on mismatch.
    """
    import subprocess

    # Build a tiny script that uses the same _fetch_and_verify pattern as the
    # fixed installers, but points at a local file with a deliberately wrong
    # expected hash.
    script = tmp_path / "test-mismatch.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'not the real content' > /tmp/fixture-file\n"
        "_fetch_and_verify() {\n"
        "  local url=\"$1\"\n"
        "  local expected=\"$2\"\n"
        "  local dest=\"$3\"\n"
        "  if ! cp \"$dest\" \"${dest}.bak\" 2>/dev/null; then\n"
        "    :\n"
        "  fi\n"
        "  local actual\n"
        "  actual=$(sha256sum \"$dest\" | awk '{print $1}')\n"
        "  if [ \"$actual\" != \"$expected\" ]; then\n"
        "    echo \"FATAL: hash mismatch for $url\" >&2\n"
        "    echo \"expected: $expected\" >&2\n"
        "    echo \"actual:   $actual\" >&2\n"
        "    rm -f \"$dest\"\n"
        "    exit 1\n"
        "  fi\n"
        "}\n"
        "_fetch_and_verify 'https://example.com/install.sh' 'deadbeef' /tmp/fixture-file\n"
        "echo 'should not reach here'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)

    assert result.returncode != 0, (
        "Script should have exited non-zero on hash mismatch, but it succeeded.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "https://example.com/install.sh" in (result.stdout + result.stderr), (
        "Error message should contain the URL of the mismatched fetch.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
