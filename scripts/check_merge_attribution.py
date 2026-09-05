#!/usr/bin/env python3
"""Reconcile fleet merge audit log against actual merges from the GitHub API.

Enumerates merged PRs (since the cutoff) via `gh pr list --state merged` and
asserts each has a matching audit entry. An unmatched merge is the signal that
an unattributed action occurred -- e.g. a squash merge with no audit line.

Reconciliation is on the (repo, mergeCommit OID) pair from the GitHub API
(`gh repo view --json nameWithOwner` and `gh pr view <n> --json mergeCommit`),
where the OID for a squash merge is the new commit on the base branch. The repo
is part of the key because the default audit log is one file shared by every
repo the fleet merges. Squash merges are visible because we enumerate from the
API, not from `git log --merges` (which never sees them).

A --cutoff (ISO timestamp or git SHA) keeps pre-adoption merges out of scope.
Cutoffs and merge timestamps are normalised to UTC and compared as instants,
never as strings: `git log --format=%cI` reports a SHA cutoff in the
COMMITTER'S LOCAL OFFSET while `mergedAt` is always UTC, and a lexicographic
compare of the two mis-orders them (see `_parse_iso_utc`).

Exit codes:
    0  PASS  -- all merges have matching audit entries
    1  FAIL  -- one or more merges lack audit entries
    2  ERROR -- infrastructure failure
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

# `gh` talks to the network; without a bound a hung call hangs CI forever
# instead of surfacing as EXIT_ERROR.
DEFAULT_GH_TIMEOUT = 60.0
GIT_TIMEOUT = 30.0


def _gh(*args: str, cwd: Path | None = None, timeout: float = DEFAULT_GH_TIMEOUT) -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"gh {' '.join(args)} timed out after {timeout:g}s") from exc
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def _parse_iso_utc(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None.

    The two sources this script compares are in different formats. `mergedAt`
    from the GitHub API is UTC with a trailing `Z`, which
    `datetime.fromisoformat` only accepts from Python 3.11, so the `Z` is
    rewritten to `+00:00` first. A cutoff resolved from a git SHA comes from
    `git log --format=%cI` and carries the committer's LOCAL offset. Comparing
    those as strings orders `2026-08-28T13:00:00Z` BEFORE
    `2026-08-28T14:00:00+02:00` even though both name 12:00Z and 13:00Z, which
    drops an in-scope merge out of the audit -- a bypass the size of the
    offset. Normalising both to UTC here makes the comparison an instant
    comparison.

    A timestamp with no offset at all is read as UTC.
    """
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_cutoff(repo: Path, cutoff: str) -> datetime:
    """Resolve a cutoff to an aware UTC datetime.

    If *cutoff* is resolvable by git, its commit timestamp is used. Otherwise
    *cutoff* itself is parsed as an ISO-8601 date or timestamp. A cutoff that
    is neither raises RuntimeError (-> EXIT_ERROR): passing it through to a
    comparison it cannot satisfy would put every merge out of scope and report
    a clean audit.
    """
    raw = cutoff
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", cutoff],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT,
        )
        ts = result.stdout.strip()
        if ts:
            raw = ts
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    parsed = _parse_iso_utc(raw)
    if parsed is None:
        raise RuntimeError(
            f"cannot resolve --cutoff {cutoff!r}: not a git ref in {repo} and not an ISO-8601 timestamp"
        )
    return parsed


def _merged_at_in_scope(pr: dict, cutoff: datetime) -> bool:
    """Whether *pr* merged at or after *cutoff*.

    A PR whose `mergedAt` is missing or unparseable is kept IN scope: dropping
    it would hide a merge from the audit, and an extra unmatched-merge report
    is noise where a silent drop is a bypass.
    """
    merged_at = _parse_iso_utc(str(pr.get("mergedAt") or ""))
    if merged_at is None:
        return True
    return merged_at >= cutoff


def find_merged_prs(
    repo: Path, cutoff: str | None = None, gh_timeout: float = DEFAULT_GH_TIMEOUT,
) -> list[dict]:
    """Return merged PRs from the GitHub API, optionally after *cutoff*.

    Enumerates via `gh pr list --state merged` so squash merges (which produce
    no merge commit in `git log --merges`) are visible. If *cutoff* is given,
    only PRs merged at or after the cutoff instant are returned.
    """
    cutoff_dt: datetime | None = None
    if cutoff:
        cutoff_dt = _resolve_cutoff(repo, cutoff)

    args: list[str] = [
        "pr", "list",
        "--state", "merged",
        "--json", "number,mergeCommit,mergedAt",
        "--limit", "1000",
    ]
    if cutoff_dt is not None:
        # GitHub search takes the qualifier and its value with no space
        # between them; a space makes `merged:>=` an empty qualifier and the
        # date a free-text term, which silently narrows the result set.
        # The value is the UTC normalisation, never the raw local-offset
        # form `%cI` hands back.
        args.extend(["--search", f"merged:>={cutoff_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}"])

    out = _gh(*args, cwd=repo, timeout=gh_timeout)
    try:
        prs = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh pr list returned unparseable JSON: {exc}") from exc

    # Client-side filter as a safety net (the --search flag may be ignored
    # by a stub gh in tests, or may differ in real gh's date parsing).
    if cutoff_dt is not None:
        prs = [p for p in prs if _merged_at_in_scope(p, cutoff_dt)]

    return prs


def read_audit_log(audit_file: Path) -> list[dict]:
    """Read JSONL audit log entries."""
    if not audit_file.exists():
        return []
    entries: list[dict] = []
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # Skipping a bad line is fail-CLOSED: the merge it described stays
        # unmatched and is reported. Say so rather than dropping it in
        # silence, so a producer emitting bad JSON is visible.
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(f"WARN: unparseable audit line in {audit_file}: {line[:120]}", file=sys.stderr)
            continue
        # `null`, arrays, strings and numbers are all valid JSON and none of
        # them has `.get`; letting one through crashes reconcile() with an
        # AttributeError that main() does not map to EXIT_ERROR.
        if not isinstance(entry, dict):
            print(f"WARN: audit line is not an object in {audit_file}: {line[:120]}", file=sys.stderr)
            continue
        entries.append(entry)
    return entries


def _extract_merge_commit_oid(pr: dict) -> str:
    """Extract the mergeCommit SHA from a PR dict.

    `gh` returns mergeCommit as a Commit object {oid: ...}; handle both
    that and a bare string.
    """
    mc = pr.get("mergeCommit")
    if isinstance(mc, dict):
        return mc.get("oid", "")
    if isinstance(mc, str):
        return mc
    return ""


def resolve_repo_slug(repo: Path, gh_timeout: float = DEFAULT_GH_TIMEOUT) -> str:
    """Return the `owner/name` slug of the checkout at *repo*.

    This is the same key `gate_merge.sh` records in the audit entry's `repo`
    field, resolved the same way (`gh repo view --json nameWithOwner`). A
    failure raises RuntimeError (-> EXIT_ERROR): without a repo identity the
    reconciliation cannot tell its own merges from another repo's.
    """
    slug = _gh(
        "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner",
        cwd=repo, timeout=gh_timeout,
    ).strip()
    if not slug:
        raise RuntimeError(f"gh repo view returned no nameWithOwner for {repo}")
    return slug


def reconcile(
    repo: Path,
    audit_file: Path,
    cutoff: str | None = None,
    gh_timeout: float = DEFAULT_GH_TIMEOUT,
) -> list[str]:
    """Return descriptions of merged PRs that lack a matching audit entry.

    Each description is ``"#{pr} {short_sha}"`` so the caller can print
    a human-readable unmatched-merge report.

    Attribution is keyed on ``(repo, sha)``, not on ``sha`` alone. The default
    audit log (`~/.fleet/merge-audit.jsonl`) is one file shared by every repo
    the fleet merges, so an entry written for a fork or mirror carrying the
    same commit OID would otherwise stand in as proof for a merge here that
    nobody attributed. An entry whose `repo` is missing or does not match is
    not proof for this checkout.
    """
    repo_slug = resolve_repo_slug(repo, gh_timeout=gh_timeout)
    merged_prs = find_merged_prs(repo, cutoff, gh_timeout=gh_timeout)
    audit_entries = read_audit_log(audit_file)
    audit_shas = {
        e.get("sha", "")
        for e in audit_entries
        if e.get("sha") and e.get("repo") == repo_slug
    }

    unmatched: list[str] = []
    for pr in merged_prs:
        sha = _extract_merge_commit_oid(pr)
        if sha and sha not in audit_shas:
            pr_num = pr.get("number", "unknown")
            short = sha[:12] if len(sha) >= 12 else sha
            unmatched.append(f"#{pr_num} {short}")
    return sorted(unmatched)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile fleet merge audit log against actual merges from the GitHub API",
    )
    parser.add_argument("--repo", type=Path, required=True, help="Git repo root (for gh auto-detect and git log)")
    parser.add_argument(
        "--audit-file",
        type=Path,
        required=True,
        help="JSONL audit log path",
    )
    parser.add_argument(
        "--cutoff",
        type=str,
        default=None,
        help="Only reconcile merges after this date (ISO 8601) or git SHA",
    )
    parser.add_argument(
        "--gh-timeout",
        type=float,
        default=DEFAULT_GH_TIMEOUT,
        help=f"Seconds to wait for each gh call before erroring (default: {DEFAULT_GH_TIMEOUT:g})",
    )
    args = parser.parse_args(argv)

    if not args.repo.is_dir():
        print(f"ERROR: repo not found: {args.repo}", file=sys.stderr)
        return EXIT_ERROR

    try:
        unmatched = reconcile(args.repo, args.audit_file, args.cutoff, gh_timeout=args.gh_timeout)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if unmatched:
        for desc in unmatched:
            print(f"UNMATCHED MERGE: {desc} -- no audit entry found")
        return EXIT_FAIL

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
