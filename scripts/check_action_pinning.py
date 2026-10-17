#!/usr/bin/env python3
"""Action-pinning guard: every workflow `uses:` must name an immutable commit.

A git tag is movable. If the owner of an action repository (or anyone who
compromises it) retargets `v7`, every workflow referencing `@v7` runs the new
code on its next run. taOS runs `pull_request_target` workflows with
repository context, and the gate workflows themselves check out code with
these actions, so a retargeted tag is a direct path into the gates (tsk-h4lvuu).

This guard scans `.github/workflows/*.yml` / `*.yaml` and FAILS when any
`uses:` reference is not pinned to an immutable ref:

  - `owner/repo@<ref>` and `owner/repo/path@<ref>`: `<ref>` must be a full
    40-hex commit SHA. Tags, branches and short SHAs fail.
  - `docker://image@sha256:<64 hex>`: a docker reference must carry a digest.
  - `./local/action`: local actions live in this repo and are pinned by the
    checkout itself, so they pass.

The assertion is on the REF SHAPE, never on a trailing `# v7` comment: an
unpinned `uses:` that happens to carry a version comment still fails. Keep the
tag as a trailing comment for humans and Dependabot:

    - uses: actions/checkout@<40-char-sha>  # v7

Usage:
    python scripts/check_action_pinning.py
    python scripts/check_action_pinning.py --repo-root /path/to/repo
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = Path(".github") / "workflows"

# `uses:` as a step key (`- uses: x`) or a job key (`uses: x` for reusable
# workflows). The value may be quoted and may carry a trailing comment.
_USES_RE = re.compile(
    r"""^\s*(?:-\s+)?uses:\s*(?P<q>['"]?)(?P<value>[^\s'"#]+)(?P=q)\s*(?:\#.*)?$"""
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DOCKER_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    value: str


def is_pinned(value: str) -> bool:
    """True if a `uses:` value references an immutable ref."""
    if value.startswith("./"):
        return True
    if value.startswith("docker://"):
        return bool(_DOCKER_DIGEST_RE.search(value))
    _, sep, ref = value.rpartition("@")
    return bool(sep) and bool(_SHA_RE.match(ref))


def check_text(text: str, path: str) -> list[Violation]:
    """Return the unpinned `uses:` references in one workflow file's text."""
    violations: list[Violation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = _USES_RE.match(line)
        if m and not is_pinned(m.group("value")):
            violations.append(Violation(path, lineno, m.group("value")))
    return violations


def workflow_files(repo_root: Path) -> list[Path]:
    wf_dir = repo_root / WORKFLOWS_DIR
    if not wf_dir.is_dir():
        return []
    return sorted(p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml"))


def check_action_pinning(repo_root: Path) -> list[Violation]:
    """Return every unpinned `uses:` under `repo_root` (empty == clean)."""
    violations: list[Violation] = []
    for wf in workflow_files(repo_root):
        rel = wf.relative_to(repo_root).as_posix()
        violations.extend(check_text(wf.read_text(encoding="utf-8"), rel))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repository root whose workflows to check (default: this checkout).",
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)

    if not workflow_files(repo_root):
        # A guard that finds nothing to scan is measuring nothing: fail loudly
        # rather than print a confident clean.
        print(f"ACTION-PINNING FAIL: no workflow files under {repo_root / WORKFLOWS_DIR}")
        return 1

    violations = check_action_pinning(repo_root)
    if not violations:
        print("action-pinning-guard: clean, every uses: is pinned to an immutable ref")
        return 0

    print(
        f"ACTION-PINNING FAIL: {len(violations)} uses: reference(s) not pinned to a "
        "40-char commit SHA:"
    )
    for v in violations:
        print(f"  - {v.path}:{v.line}: {v.value}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
