#!/usr/bin/env python3
"""Secret-material ignore guard.

Verifies that the committed `.gitignore` on the branch under test still
protects known secret-shaped paths. After a dev->master promotion a
`.gitignore` rule can be silently dropped during conflict resolution (a
`.gitignore` is exactly the kind of file a rebase conflict quietly loses while
every test still passes and nothing builds red), so this gate asserts the
protection mechanically instead of assuming promotion carried it.

The check runs on push to `master`, `dev` and `release/*` -- so a dropped
pattern fails the branch it actually lands on -- and on PRs targeting those
branches -- so a conflict-resolution loss fails BEFORE the merge. See
`.github/workflows/secret-ignores-gate.yml`.

Two independent signals, defense in depth:

  1. REQUIRED_PATTERNS. Each entry must appear verbatim as an active rule line
     in `.gitignore`. A rule line is a non-blank, non-comment line (comment =
     first non-whitespace char is `#`). Matching the exact line (rather than a
     loose substring across the file) is deliberate: it ignores the comment
     prose that discusses these patterns and treats a different-but-similar
     rule (e.g. `data/*.key`) as NOT satisfying the `master`-level `*.key` rule.
     Removing any required rule line turns the gate red deterministically -- this
     is the "prove it goes red by removing one pattern" mechanism.

  2. SECRET_PATHS. Each path must be reported as ignored by `git check-ignore`,
     i.e. the protection actually takes effect. This catches holes that line
     presence cannot (a rule that is present but malformed, or a path that no
     longer matches), and is the "secret-shaped paths are ALL ignored" check
     from tsk-laezfg.

Usage:
    python scripts/check_secret_ignores.py
    python scripts/check_secret_ignores.py --repo-root /path/to/repo
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE_NAME = ".gitignore"

# Protection rules whose absence must fail the gate. These are the secret-
# material rules added in response to data/hub/identity.json being committed to
# PR #2043 with signing_private and encryption_private in plaintext (#2171,
# #2173). Each is matched as an exact active rule line so a dropped rule is
# detected even when its text is still mentioned in a comment or covered by a
# narrower sibling rule.
REQUIRED_PATTERNS = [
    "*.key",
    "*_private.pem",
    "*_private.key",
    "*_private.json",
    "*_private_key*",
    "identity.json",
    "*.p8",
    "*credentials.json",
    "*creds*.json",
    "secrets/",
    "*.token",
    "*.cred",
    "data/.secrets_key",
    "data/.seeded-agent-tokens.json",
    "data/secrets.db*",
    "data/*.key",
    "data/*.token",
    "data/hub/",
    # LiteLLM proxy master key: bare `_key` suffix, matched by NO glob above
    # (data/*.key needs a `.key` suffix). It sat live and unignored on dev
    # boxes until this rule (JAY-QUEUE item, 2026-08-08).
    "data/.litellm_master_key",
]

# Secret-shaped paths that must actually be ignored by `git check-ignore`. Each
# is annotated with its primary protecting rule. Not every path is exclusive
# (some are double-protected by design); the REQUIRED_PATTERNS check above is
# what makes every single pattern's removal detectable. These paths assert the
# protection has real teeth on the branch under test.
SECRET_PATHS = [
    "data/hub/identity.json",       # data/hub/ , identity.json
    "identity.json",                # identity.json
    "foo.key",                      # *.key
    "x.p8",                         # *.p8
    "creds.json",                   # *creds*.json
    "y_credentials.json",           # *credentials.json (also *creds*.json)
    "app_private.pem",              # *_private.pem
    "app_private.json",             # *_private.json
    "signing_private_key.pem",      # *_private_key*
    "foo.token",                    # *.token
    "foo.cred",                     # *.cred
    "secrets/foo.token",            # secrets/
    "data/.secrets_key",            # data/.secrets_key
    "data/secrets.db-wal",          # data/secrets.db*
    "data/.litellm_master_key",     # data/.litellm_master_key (exact)
]


@dataclass
class Violation:
    kind: str  # "pattern" or "path"
    detail: str


def _run_git(args: list[str], repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def _read_gitignore(repo_root: Path) -> str:
    path = repo_root / GITIGNORE_NAME
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _active_rule_lines(gitignore_text: str) -> list[str]:
    """Return the active (non-blank, non-comment) rule lines, stripped.

    A `.gitignore` rule line is an active rule unless it is blank or its first
    non-whitespace character is `#` (a comment). Trailing/leading whitespace is
    stripped so cosmetic reformatting does not make the gate trip.
    """
    lines: list[str] = []
    for raw in gitignore_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def check_patterns(gitignore_text: str) -> list[str]:
    """Return the required patterns absent from the .gitignore text."""
    active = _active_rule_lines(gitignore_text)
    missing: list[str] = []
    for pattern in REQUIRED_PATTERNS:
        if pattern not in active:
            missing.append(pattern)
    return missing


def is_path_ignored(path: str, repo_root: Path) -> bool:
    """True if `git check-ignore` reports `path` as ignored on `repo_root`."""
    result = _run_git(["check-ignore", "--quiet", path], repo_root)
    return result.returncode == 0


def check_paths(repo_root: Path) -> list[str]:
    """Return secret-shaped paths that are NOT ignored on `repo_root`."""
    not_ignored: list[str] = []
    for path in SECRET_PATHS:
        if not is_path_ignored(path, repo_root):
            not_ignored.append(path)
    return not_ignored


def check_secret_ignores(repo_root: Path = REPO_ROOT) -> list[Violation]:
    """Return all violations on `repo_root` (empty == clean)."""
    text = _read_gitignore(repo_root)
    violations: list[Violation] = [
        Violation("pattern", p) for p in check_patterns(text)
    ]
    # `git check-ignore` requires a usable git directory; a missing .git is
    # treated as "nothing is ignored" so every secret path is a violation.
    if not is_usable_git_repo(repo_root):
        violations.extend(Violation("path", p) for p in SECRET_PATHS)
        return violations
    violations.extend(Violation("path", p) for p in check_paths(repo_root))
    return violations


def is_usable_git_repo(repo_root: Path) -> bool:
    """True if `repo_root` is a git working tree we can check-ignore against."""
    result = _run_git(["rev-parse", "--is-inside-work-tree"], repo_root)
    return result.returncode == 0 and result.stdout.strip() == "true"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repository root whose .gitignore to check (default: this checkout).",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)

    violations = check_secret_ignores(repo_root)
    if not violations:
        print("secret-ignores-guard: clean")
        return 0

    patterns = sorted(v.detail for v in violations if v.kind == "pattern")
    paths = sorted(v.detail for v in violations if v.kind == "path")
    if patterns:
        print("SECRET-IGNORE FAIL: .gitignore is missing required protection patterns:")
        for p in patterns:
            print(f"  - {p}")
    if paths:
        print("SECRET-IGNORE FAIL: these secret-shaped paths would NOT be ignored:")
        for p in paths:
            print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
