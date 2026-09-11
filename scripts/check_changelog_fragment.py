#!/usr/bin/env python3
"""Changelog fragment gate.

Blocks PRs that change user-visible code without a matching changelog fragment or CHANGELOG.md line,
unless the change carries an explicit "Changelog-Not-Needed: <why>" trailer.

One layer: diff-gate -- path -> changelog rule engine. A configured rule fires
when a non-test file under tinyagentos/ or desktop/src/ (any depth, including docs and assets)
is added or modified and there is no corresponding changelog.d/ fragment or CHANGELOG.md line.

The escape hatch is a "Changelog-Not-Needed: <reason>" commit-message trailer.
If the trailer is present, the gate prints which commit used it and who authored it,
then passes all rules for that PR.

Usage:
    python scripts/check_changelog_fragment.py --staged
    python scripts/check_changelog_fragment.py --base origin/dev
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "docs" / "doc-gate.toml"
DEFAULT_TRAILER = "Changelog-Not-Needed:"

# Exit codes: 0 clean, 1 a changelog-gate violation, 2 a CLI/usage error, 3 a config error
EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_CONFIG_ERROR = 3
EXIT_GIT_ERROR = 4

# Non-test files: any file under tinyagentos/ or desktop/src/ (including docs and assets)
TINYAGENTOS_PREFIX = "tinyagentos/"
DESKTOP_SRC_PREFIX = "desktop/src/"

# Glob patterns: changelog.d/*.md and CHANGELOG.md
CHANGELOG_FRAG_GLOB = "changelog.d/*.md"
CHANGELOG_FILE = "CHANGELOG.md"


def get_trailer(config: dict) -> str:
    """Single source of truth for the commit-message trailer prefix."""
    return config.get("gate", {}).get("changelog_trailer", DEFAULT_TRAILER)


def _is_test_path(path: str) -> bool:
    """Return True if the path is a test file (should be exempt)."""
    base = path.rsplit("/", 1)[-1]
    if "/__tests__/" in path:
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    return base.endswith((".test.tsx", ".test.ts", ".test.jsx", ".test.js", ".spec.tsx", ".spec.ts"))


def _match_any(path: str, patterns: list[str]) -> bool:
    """Check if path matches any glob pattern."""
    return any(_glob_match(path, pat) for pat in patterns)


def _glob_match(path: str, pattern: str) -> bool:
    """Path-segment-aware glob match, unlike fnmatch (where * crosses /)."""
    regex_parts = []
    i = 0
    length = len(pattern)
    while i < length:
        char = pattern[i]
        if char == "*":
            if i + 1 < length and pattern[i + 1] == "*":
                if regex_parts and regex_parts[-1] == "/" and i + 2 == length:
                    regex_parts[-1] = "(?:/.*)?"
                else:
                    regex_parts.append(".*")
                i += 2
            else:
                regex_parts.append("[^/]*")
                i += 1
        elif char == "?":
            regex_parts.append("[^/]")
            i += 1
        else:
            regex_parts.append(re.escape(char))
            i += 1
    return re.fullmatch("".join(regex_parts), path) is not None


def _run_git(args: list[str], ref: str | None = None) -> str:
    """Run git command and return output."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        msg = f"git {' '.join(args)} failed"
        if ref:
            msg += f" (ref: {ref})"
        raise RuntimeError(msg) from None


def _parse_name_status(output: str) -> list[tuple[str, str]]:
    """Parse `git diff --name-status` output into list of (status, path)."""
    changed: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        changed.append((status[0], path))
    return changed


def _git_changed_staged() -> list[tuple[str, str]]:
    return _parse_name_status(_run_git(["diff", "--cached", "--name-status"]))


def _git_changed_base(base_ref: str) -> list[tuple[str, str]]:
    return _parse_name_status(_run_git(["diff", "--name-status", f"{base_ref}...HEAD"], ref=base_ref))


def _git_commits_with_messages(base_ref: str) -> list[tuple[str, str, str]]:
    """Return (hash, author_name, message_body) for each commit in the range."""
    out = _run_git(["log", f"{base_ref}..HEAD", "--format=%H%x1f%an%x1f%B%x1e"], ref=base_ref)
    commits: list[tuple[str, str, str]] = []
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        fields = record.lstrip("\n").split("\x1f")
        if len(fields) < 3:
            continue
        commit_hash, author, body = fields[0], fields[1], fields[2]
        commits.append((commit_hash.strip(), author, body))
    return commits


def _log_trailer_usage(commits: list[tuple[str, str, str]], trailer: str) -> None:
    """Print a log line for each commit that carries a non-empty trailer."""
    for commit_hash, author, message in commits:
        for line in message.splitlines():
            stripped = line.strip()
            if stripped.startswith(trailer) and stripped[len(trailer):].strip():
                short_hash = commit_hash[:8]
                why = stripped[len(trailer):].strip()
                print(f"changelog-gate: trailer override used in {short_hash} by {author}: {why}")
                break


def evaluate_rules(
    changed_status: list[tuple[str, str]],
    commit_messages: list[str],
    config: dict,
    pin_only_paths: set[str] | None = None,
) -> list[str]:
    """Run the changelog fragment rule against a changeset.
    
    Non-test files under tinyagentos/ or desktop/src/ (including docs and assets)
    trigger a rule when added or modified. The rule is satisfied if either:
      - a file under CHANGELOG.md (exact match) exists in all_paths
      - a file matching changelog.d/*.md exists in all_paths
      - a trailer "Changelog-Not-Needed: <reason>" appears in any commit message.
    """
    trailer = get_trailer(config)
    
    if pin_only_paths is None:
        pin_only_paths = set()
    
    all_paths = [path for status, path in changed_status if status in ("A", "M")]
    
    # Check if any non-test files under tinyagentos/ or desktop/src/ changed
    has_non_test_change = any(
        not _is_test_path(path) and (
            path.startswith(TINYAGENTOS_PREFIX) or path.startswith(DESKTOP_SRC_PREFIX)
        )
        for status, path in changed_status
        if status in ("A", "M")
    )
    
    if not has_non_test_change:
        # No rule triggered
        return []
    
    # Check if rule is satisfied
    trailer_present = any(
        line.strip().startswith(trailer) and line.strip()[len(trailer):].strip()
        for message in commit_messages
        for line in message.splitlines()
    )
    
    if trailer_present:
        return []
    
    # Check if CHANGELOG.md or a fragment exists in all_paths
    changelog_satisfied = any(
        path == CHANGELOG_FILE or _glob_match(path, CHANGELOG_FRAG_GLOB)
        for path in all_paths
    )
    
    if changelog_satisfied:
        return []
    
    # Rule violated: non-test change under tinyagentos/ or desktop/src/ but no changelog entry
    hint = "user-visible behaviour changed; add a changelog.d/<pr>-<slug>.md fragment (preferred) or a CHANGELOG.md line (or add a Changelog-Not-Needed trailer explaining why not)"
    return [f"changelog -- {hint} (add or modify: {CHANGELOG_FILE} or {CHANGELOG_FRAG_GLOB}, or add a '{trailer} <reason>' trailer)"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    subparsers = parser.add_subparsers(dest="command", required=True)
    diff_parser = subparsers.add_parser("diff-gate", help="Run changelog rule engine")
    group = diff_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="Check the git index (pre-commit)")
    group.add_argument("--base", help="Compare <base>...HEAD (CI / commit-msg)")

    args = parser.parse_args(argv)
    try:
        config = tomllib.load(open(args.config, "rb"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"changelog-gate: config error: {args.config}: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # diff-gate
    try:
        if args.staged:
            changed = _git_changed_staged()
            commit_messages: list[str] = []
        else:
            changed = _git_changed_base(args.base)
            commits_meta = _git_commits_with_messages(args.base)
            commit_messages = [msg for _hash, _author, msg in commits_meta]
            trailer = get_trailer(config)
            _log_trailer_usage(commits_meta, trailer)
    except RuntimeError as e:
        print(f"changelog-gate: git error: {e}", file=sys.stderr)
        return EXIT_GIT_ERROR

    # A workflow-file change that only bumps `uses:` action pins (a dependabot
    # version bump) is not a changelog change: detect it here, against the live
    # diff, so bot-authored bumps can go green without a fragment that the bot
    # cannot author. Author is NOT consulted -- a substantive changelog edit
    # by any author still fails.
    # For changelog, we don't need pin-only detection because changelog.d/ is
    # a separate directory that only contains fragments, not workflow files.
    pin_only_paths = set()

    failures = evaluate_rules(changed, commit_messages, config, pin_only_paths=pin_only_paths)

    if not failures:
        print("changelog-gate: clean")
        return EXIT_OK
    for failure in failures:
        print(f"CHANGELOG-GATE FAIL: {failure}")
    return EXIT_VIOLATION


if __name__ == "__main__":
    sys.exit(main())
