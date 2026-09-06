#!/usr/bin/env python3
"""Documentation-drift gate.

Blocks commits/PRs that change feature code without a matching doc update,
unless the change carries an explicit "Docs-Reviewed: <why>" trailer.

Two layers:
  invariants  -- deterministic sanity checks (Layer A). Currently: every
                 scripts/, tinyagentos/, docs/, desktop/ path mentioned in the
                 configured doc set actually exists on disk.
  diff-gate   -- path -> doc rule engine (Layer B). A configured rule fires
                 when a *structural* change matches one of its `when_changed`
                 globs. By default added, deleted, renamed, and copied files
                 (status A/D/R/C) are structural; set `on_modify = true` on a
                 rule to also count plain modifications (status M) for that
                 rule. Only ADDED or MODIFIED files (status A/M) can *satisfy*
                 a rule by matching a require_doc glob; a renamed, copied, or
                 deleted require_doc does NOT count.

  A workflow-file modification whose diff changes only `uses: <action>@<ref>`
  pins -- a pure version bump, as a dependency bot produces -- is treated as
  non-structural, so it does not trip the contributor-skill rule on its own.
  Any other changed line in the same file keeps the change substantive,
  regardless of author (the exemption is content-based, not identity-based).

Config lives in docs/doc-gate.toml. Rules are data, not code: add more by
editing the TOML, no changes to this file required.

Usage:
    python scripts/check_doc_gate.py invariants
    python scripts/check_doc_gate.py diff-gate --staged
    python scripts/check_doc_gate.py diff-gate --base origin/dev
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
DEFAULT_TRAILER = "Docs-Reviewed:"

# Exit codes: 0 clean, 1 a doc-gate violation, 2 a CLI/usage error (reserved
# for argparse, never our own code), 3 a config error (broken, missing, or
# unparseable config).  3 is kept off 2 so a typo'd flag is never mistaken for
# a bad config: a misconfigured gate must be distinguishable from both a real
# documentation-drift violation and a usage mistake.  4 is a git infrastructure
# failure (missing ref, network error, shallow clone, etc.) so it is never
# confused with a rule violation.
EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_CONFIG_ERROR = 3
EXIT_GIT_ERROR = 4

# A path-like token: one of the four known repo prefixes followed by a run of
# non-whitespace / non-quoting characters. The negative lookbehind stops us
# matching a prefix that is actually embedded inside a larger path (e.g. the
# "tinyagentos/" inside "/home/<user>/tinyagentos/data/" in a deploy-layout
# table), which would otherwise falsely flag deploy-time paths that never
# exist in the repo itself. `-` is in the lookbehind for the same reason:
# home-dir slugs like "-*-tinyagentos/memory/MEMORY.md" embed a repo prefix
# after a hyphen, and the glob `*` sits BEFORE the match so the glob filter
# never sees it. `)` and `]` are excluded from the token body so a markdown
# link's closing bracket ends the token instead of gluing the URL on.
_TOKEN_RE = re.compile(r"(?<![\w/-])(?:scripts|tinyagentos|docs|desktop)/[^\s`\"'|)\]]+")

# Chars that mark a token as a glob pattern or a <placeholder> rather than a
# concrete repo path -- these are never asserted to exist.
_GLOB_OR_PLACEHOLDER_CHARS = set("*?[]{}<>$~")

# Trailing punctuation that is prose/markdown decoration, not part of the path.
_TRAILING_PUNCT = ".,;:!?)]}'\"`"

# A line-citation suffix: `:123`, `:12-20`, `:1,5-9`, and repeated groups so
# file:line:col (ripgrep, compiler output) collapses in one pass. Applied
# AFTER the trailing-punct strip — the anchor is defeated by a trailing full
# stop or comma otherwise, and citations ending a sentence are the common case.
_LINE_SUFFIX_RE = re.compile(r"(?::[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*)+$")


def _clean_token(raw: str) -> str | None:
    """Normalize a raw regex match into a bare repo-relative path, or None
    if it should be ignored (glob, placeholder, anchor/query fragment)."""
    token = raw
    # "::" is a symbol reference (file.py::function), not part of the path.
    for sep in ("#", "?", "::"):
        if sep in token:
            token = token.split(sep, 1)[0]
    while token and token[-1] in _TRAILING_PUNCT:
        token = token[:-1]
    token = _LINE_SUFFIX_RE.sub("", token)
    if not token:
        return None
    if any(c in _GLOB_OR_PLACEHOLDER_CHARS for c in token):
        return None
    return token


def extract_path_tokens(text: str) -> list[str]:
    """Pull candidate repo-relative path tokens out of doc prose."""
    tokens = []
    for m in _TOKEN_RE.finditer(text):
        cleaned = _clean_token(m.group(0))
        if cleaned:
            tokens.append(cleaned)
    return tokens


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _validate_config(config: dict) -> None:
    """Validate the structural shape of a parsed doc-gate config.

    A config that parses as valid TOML but has the wrong shape (e.g.
    ``rules = "not a list"``) is a config error, not a runtime crash: surface
    it as EXIT_CONFIG_ERROR rather than letting it die in the rule loop with
    an AttributeError. Also validates that rule members have correct types
    (name, when_changed, require_doc, hint are strings, when_changed and
    require_doc lists contain only strings, on_modify is boolean). Validates
    that invariants.referenced_paths_scan and ignore_tokens contain only
    strings.
    """
    if not isinstance(config, dict):
        raise ValueError("config root must be a table")
    rules = config.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("'rules' must be a list of tables")
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"rules[{i}] must be a table")
        # Validate rule members
        if "name" in rule and not isinstance(rule["name"], str):
            raise ValueError(f"rules[{i}].name must be a string")
        if "when_changed" in rule and not isinstance(rule["when_changed"], list):
            raise ValueError(f"rules[{i}].when_changed must be a list")
        elif "when_changed" in rule:
            for j, item in enumerate(rule["when_changed"]):
                if not isinstance(item, str):
                    raise ValueError(f"rules[{i}].when_changed[{j}] must be a string")
        if "require_doc" in rule and not isinstance(rule["require_doc"], list):
            raise ValueError(f"rules[{i}].require_doc must be a list")
        elif "require_doc" in rule:
            for j, item in enumerate(rule["require_doc"]):
                if not isinstance(item, str):
                    raise ValueError(f"rules[{i}].require_doc[{j}] must be a string")
        if "hint" in rule and not isinstance(rule["hint"], str):
            raise ValueError(f"rules[{i}].hint must be a string")
        if "on_modify" in rule and not isinstance(rule["on_modify"], bool):
            raise ValueError(f"rules[{i}].on_modify must be a boolean")
    gate = config.get("gate", {})
    if not isinstance(gate, dict):
        raise ValueError("'gate' must be a table")
    if "trailer" in gate and not isinstance(gate["trailer"], str):
        raise ValueError("'gate.trailer' must be a string")
    invariants = config.get("invariants", {})
    if not isinstance(invariants, dict):
        raise ValueError("'invariants' must be a table")
    scan = invariants.get("referenced_paths_scan", [])
    if not isinstance(scan, list):
        raise ValueError("'invariants.referenced_paths_scan' must be a list")
    # Validate scan list entries are strings
    for j, item in enumerate(scan):
        if not isinstance(item, str):
            raise ValueError(f"invariants.referenced_paths_scan[{j}] must be a string")
    ignore = invariants.get("ignore_tokens", [])
    if not isinstance(ignore, list):
        raise ValueError("'invariants.ignore_tokens' must be a list")
    # Validate ignore list entries are strings
    for j, item in enumerate(ignore):
        if not isinstance(item, str):
            raise ValueError(f"invariants.ignore_tokens[{j}] must be a string")
    required_sections = invariants.get("required_sections", [])
    if not isinstance(required_sections, list):
        raise ValueError("'invariants.required_sections' must be a list")
    for i, entry in enumerate(required_sections):
        if not isinstance(entry, dict):
            raise ValueError(f"invariants.required_sections[{i}] must be a table")
        if "doc" in entry and not isinstance(entry["doc"], str):
            raise ValueError(f"invariants.required_sections[{i}].doc must be a string")
        if "headings" in entry and not isinstance(entry["headings"], list):
            raise ValueError(f"invariants.required_sections[{i}].headings must be a list")
        elif "headings" in entry:
            for j, item in enumerate(entry["headings"]):
                if not isinstance(item, str):
                    raise ValueError(f"invariants.required_sections[{i}].headings[{j}] must be a string")


def check_referenced_paths(repo_root: Path, files_to_scan: list[str], config: dict) -> list[str]:
    """Layer A: every scripts/tinyagentos/docs/desktop path token mentioned in
    the configured doc set must exist on disk. A scan-target file that itself
    does not exist (e.g. a local-only, gitignored doc) is silently skipped
    rather than treated as a failure.

    Scan entries may be globs (``docs/agent-manual/*.md``) -- expanded against
    the working tree, so a newly added manual page or skill is scanned without
    a config edit. ``invariants.ignore_tokens`` lists tokens that are
    deliberate tombstones (docs that EXPLAIN a file was removed must be able
    to name it without failing the gate forever).
    """
    ignore = set(config.get("invariants", {}).get("ignore_tokens", []))
    expanded: list[str] = []
    for rel in files_to_scan:
        if any(c in "*?[]" for c in rel):
            expanded.extend(
                sorted(str(p.relative_to(repo_root)) for p in repo_root.glob(rel) if p.is_file())
            )
        else:
            expanded.append(rel)
    failures: list[str] = []
    for rel in expanded:
        doc_path = repo_root / rel
        if not doc_path.is_file():
            continue
        text = doc_path.read_text(encoding="utf-8", errors="ignore")
        for token in extract_path_tokens(text):
            if token in ignore:
                continue
            if not (repo_root / token).exists():
                failures.append(f"{rel} references '{token}' which does not exist in the repo")
    return failures


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def check_required_sections(repo_root: Path, config: dict) -> list[str]:
    """Layer A: every doc listed in ``invariants.required_sections`` must
    contain each of its required headings in the working tree.

    A scan-target file that itself does not exist (e.g. a local-only,
    gitignored doc) is silently skipped rather than treated as a failure.
    """
    failures: list[str] = []
    for entry in config.get("invariants", {}).get("required_sections", []):
        doc_rel = entry.get("doc", "")
        required_headings = entry.get("headings", [])
        doc_path = repo_root / doc_rel
        if not doc_path.is_file():
            continue
        text = doc_path.read_text(encoding="utf-8", errors="ignore")
        found_headings: set[str] = set()
        for line in text.splitlines():
            m = _HEADING_RE.match(line)
            if m:
                found_headings.add(m.group(2).strip())
        for heading in required_headings:
            if heading not in found_headings:
                failures.append(
                    f"{doc_rel} is missing required section: '{heading}'"
                )
    return failures


def _glob_match(path: str, pattern: str) -> bool:
    """Path-segment-aware glob match, unlike fnmatch (where `*` crosses `/`).

    `**` matches zero or more path segments (so a trailing `/**` also matches
    the bare parent, e.g. `a/**` matches `a`), a single `*` matches within one
    path segment only (`[^/]*`), `?` matches one non-separator character
    (`[^/]`), and every other character is matched literally.
    """
    regex_parts = []
    i = 0
    length = len(pattern)
    while i < length:
        char = pattern[i]
        if char == "*":
            if i + 1 < length and pattern[i + 1] == "*":
                # A trailing `/**` should also match the bare parent path, so
                # fold the preceding literal `/` into an optional group.
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


def _match_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_match(path, pat) for pat in patterns)


def _is_test_path(path: str) -> bool:
    """A test file is never a structural feature change (a new app, route,
    etc.), so adding or removing one must not trip a doc-gate structural rule.
    Covers frontend co-located tests and Python test modules (#171)."""
    base = path.rsplit("/", 1)[-1]
    if "/__tests__/" in path:
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    return base.endswith(
        (".test.tsx", ".test.ts", ".test.jsx", ".test.js", ".spec.tsx", ".spec.ts")
    )


# A `uses:` pin line as it appears in a workflow file, after the diff's leading
# +/- marker has been stripped: optional indent, an optional YAML list marker
# (- or *), then `uses: <owner>/<repo>@<ref>`. The ref excludes whitespace and
# `#` so a trailing comment (e.g. `@<sha>  # v2.6.1`) does not poison the match.
# A `uses:` without an `@<ref>` (a non-pinned or local action) is NOT a pin
# line and keeps the change substantive.
_USES_PIN_LINE = re.compile(r"^\s*[-*]?\s*uses:\s+(?P<target>[^@\s]+)@[^@\s#]+")


def _uses_pin_target(line: str) -> str | None:
    """Return the action TARGET (the text before `@`) of a `uses:` pin line,
    or None when the line is not a pin line at all."""
    m = _USES_PIN_LINE.match(line)
    return m.group("target") if m else None


def _path_diff_is_uses_pin_only(diff_output: str) -> bool:
    """True iff `git diff --unified=0` output is a pure `uses:` pin bump --
    every content line is a `uses: <action>@<ref>` pin AND, within each hunk,
    the removed and added lines name the SAME set of action targets.

    A dependency-bot version bump changes nothing but the `@ref` on existing
    `uses:` lines -- e.g. `uses: actions/checkout@v4` -> `@v5`. Such a change
    does not alter CI behaviour, packaging, or contribution rules, so it must
    not trip the contributor-skill rule even though the file lives under
    `.github/workflows/`.

    Any other changed line -- a new step, an altered `with:` input, a `name:`
    tweak, an environment change -- makes this False, so a substantive workflow
    rewrite still fails the gate without a trailer. Author is irrelevant: a
    human who also changes only pins is exempt, and a bot that rewrites steps
    is not -- the decision is content-based, not identity-based.

    THE TARGETS MUST BE PAIRED, NOT MERELY CLASSIFIED (#2568 review). Both
    sides of

        -        uses: actions/checkout@v4
        +        uses: attacker/checkout@v1

    are syntactically pin lines, so a per-line classifier calls that a version
    bump and hands an attacker-owned action a gate exemption. Comparing the
    removed against the added targets is what makes a swapped action, a newly
    introduced action, or a removed one substantive again. Pairing is per hunk
    so a genuine bump in one hunk cannot vouch for a swap in another.
    """
    hunks: list[tuple[list[str], list[str]]] = [([], [])]
    for line in diff_output.splitlines():
        if line.startswith("@@"):
            hunks.append(([], []))
            continue
        if not line or line[0] not in "+-":
            continue
        # Skip the +++ / --- extended file headers; they are not content.
        if line.startswith(("+++", "---")):
            continue
        target = _uses_pin_target(line[1:])
        if target is None:
            return False
        hunks[-1][0 if line[0] == "-" else 1].append(target)

    for removed, added in hunks:
        if sorted(removed) != sorted(added):
            return False
    return True


def evaluate_rules(
    changed_status: list[tuple[str, str]],
    commit_messages: list[str],
    config: dict,
    pin_only_paths: set[str] | None = None,
) -> list[str]:
    """Layer B: run every configured rule against a changeset.

    changed_status: list of (status, path) pairs as from `git diff
    --name-status`, e.g. [("A", "desktop/src/apps/Foo/Foo.tsx"), ("M", "x")].
    By default only status "A" (added) or "D" (deleted) files count as
    structural change for triggering a rule. When a rule sets `on_modify =
    true`, status "M" (plain modification) also counts for that rule.
    Any added or modified file can satisfy a rule if it matches a
    require_doc glob; a deleted require_doc does NOT count. Trigger scope (A/D,
    plus M when on_modify is set) is separate from satisfaction scope (A/M only).
    Test paths are never structural and are always excluded.
    commit_messages: full text of each commit message in range (empty list
    when there is no finalized commit yet, i.e. --staged mode).
    pin_only_paths: paths whose modification changes only `uses:` action pins
    (a dependency-bot version bump); these are not structural and so never
    trigger a rule. Computed by _collect_pin_only_paths from the live diff.
    """
    trailer = get_trailer(config)
    rules = config.get("rules", [])

    if pin_only_paths is None:
        pin_only_paths = set()

    all_paths = [path for status, path in changed_status if status in ("A", "M")]

    trailer_present = any(
        line.strip().startswith(trailer) and line.strip()[len(trailer):].strip()
        for message in commit_messages
        for line in message.splitlines()
    )

    failures: list[str] = []
    for rule in rules:
        name = rule.get("name", "?")
        when_changed = rule.get("when_changed", [])
        require_doc = rule.get("require_doc", [])
        hint = rule.get("hint", "")
        on_modify = rule.get("on_modify", False)

        rule_structural_paths = [
            path for status, path in changed_status
            if (status in ("A", "D", "R", "C") or (on_modify and status == "M"))
            and not _is_test_path(path)
            and path not in pin_only_paths
        ]

        triggered = any(_match_any(p, when_changed) for p in rule_structural_paths)
        if not triggered:
            continue

        doc_edited = any(_match_any(p, require_doc) for p in all_paths)
        if doc_edited or trailer_present:
            continue

        failures.append(
            f"{name} -- {hint} (edit one of: {', '.join(require_doc)}, "
            f"or add a 'Docs-Reviewed: <why>' trailer)"
        )
    return failures


class GitCommandError(Exception):
    """Raised when a git command fails, so infrastructure failures are
    distinguishable from genuine doc-gate violations."""


def _run_git(args: list[str], ref: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        msg = f"git {' '.join(args)} failed"
        if ref:
            msg += f" (ref: {ref})"
        raise GitCommandError(msg) from None


def _parse_name_status(output: str) -> list[tuple[str, str]]:
    changed: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        # Renames/copies (R100, C100, ...) carry old + new path; the new path
        # is what matters for both triggering and satisfying a rule.
        path = parts[-1]
        changed.append((status[0], path))
    return changed


def _git_changed_staged() -> list[tuple[str, str]]:
    return _parse_name_status(_run_git(["diff", "--cached", "--name-status"]))


def _git_changed_base(base_ref: str) -> list[tuple[str, str]]:
    return _parse_name_status(_run_git(["diff", "--name-status", f"{base_ref}...HEAD"], ref=base_ref))


def _git_commit_messages(base_ref: str) -> list[str]:
    out = _run_git(["log", f"{base_ref}..HEAD", "--format=%B%x00"], ref=base_ref)
    return [m for m in out.split("\x00") if m.strip()]


def _git_commits_with_messages(base_ref: str) -> list[tuple[str, str, str]]:
    """Return (hash, author_name, message_body) for each commit in the range."""
    # %x1e terminates each commit record and %x1f separates the three fields
    # inside it. A record terminator distinct from the field separator is what
    # makes this parseable: with one separator for both, the flat split cannot
    # tell a new commit's hash from the previous commit's body.
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


def _collect_pin_only_paths(
    changed: list[tuple[str, str]], base_ref: str | None = None
) -> set[str]:
    """Return the subset of MODIFIED (status M) workflow files whose diff
    against `base_ref` changes only `uses: <action>@<ref>` pins.

    When `base_ref` is None the diff is read from the staged index (--cached),
    used by the pre-commit / commit-msg hooks; otherwise it is the
    `<base_ref>...HEAD` range used by CI. Only `.github/workflows/` files are
    inspected: those are the only paths the contributor-skill structural rule
    matches, and a pin-only change is meaningless for `pyproject.toml` or
    `CONTRIBUTING.md`, which are always re-evaluated by the rule.

    A git failure diffing a single path is treated as "not known to be
    pin-only" (that path is skipped), never as a gate failure: a workflow
    change that cannot be inspected is re-evaluated by the rule as-is, which
    is the fail-closed side. Author is never consulted.
    """
    pin_only: set[str] = set()
    range_arg = "--cached" if base_ref is None else f"{base_ref}...HEAD"
    for status, path in changed:
        if status != "M" or not path.startswith(".github/workflows/"):
            continue
        try:
            diff = _run_git(["diff", "--unified=0", range_arg, "--", path], ref=base_ref)
        except GitCommandError:
            continue
        if _path_diff_is_uses_pin_only(diff):
            pin_only.add(path)
    return pin_only


def _log_trailer_usage(commits: list[tuple[str, str, str]], trailer: str) -> None:
    """Print a log line for each commit that carries a non-empty trailer."""
    for commit_hash, author, message in commits:
        for line in message.splitlines():
            stripped = line.strip()
            if stripped.startswith(trailer) and stripped[len(trailer):].strip():
                short_hash = commit_hash[:8]
                why = stripped[len(trailer):].strip()
                print(f"doc-gate: trailer override used in {short_hash} by {author}: {why}")
                break


def get_trailer(config: dict) -> str:
    """Single source of truth for the commit-message trailer prefix, shared
    by the diff-gate check and the hooks (via the print-trailer command)."""
    return config.get("gate", {}).get("trailer", DEFAULT_TRAILER)


def _report(failures: list[str]) -> int:
    if not failures:
        print("doc-gate: clean")
        return EXIT_OK
    for failure in failures:
        print(f"DOC-GATE FAIL: {failure}")
    return EXIT_VIOLATION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("invariants", help="Run Layer-A deterministic checks")
    sub.add_parser("print-trailer", help="Print the configured commit trailer prefix")

    diff_parser = sub.add_parser("diff-gate", help="Run Layer-B path->doc rule engine")
    group = diff_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="Check the git index (pre-commit)")
    group.add_argument("--base", help="Compare <base>...HEAD (CI / commit-msg)")

    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        _validate_config(config)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError, ValueError) as e:
        print(f"doc-gate: config error: {args.config}: {e}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.command == "invariants":
        files_to_scan = config.get("invariants", {}).get("referenced_paths_scan", [])
        failures = check_referenced_paths(REPO_ROOT, files_to_scan, config)
        failures.extend(check_required_sections(REPO_ROOT, config))
        return _report(failures)

    if args.command == "print-trailer":
        print(get_trailer(config))
        return 0

    # diff-gate
    try:
        if args.staged:
            changed = _git_changed_staged()
            commit_messages: list[str] = []
        else:
            changed = _git_changed_base(args.base)
            commits_meta = _git_commits_with_messages(args.base)
            commit_messages = [msg for _hash, _author, msg in commits_meta]
            _log_trailer_usage(commits_meta, get_trailer(config))
    except GitCommandError as e:
        print(f"doc-gate: git error: {e}", file=sys.stderr)
        return EXIT_GIT_ERROR

    # A workflow-file change that only bumps `uses:` action pins (a dependabot
    # version bump) is not a contribution-rules change: detect it here, against
    # the live diff, so bot-authored bumps can go green without a trailer that
    # the bot cannot author. Author is NOT consulted -- a substantive edit by
    # any identity is still red.
    pin_only_paths = _collect_pin_only_paths(changed, args.base)

    failures = evaluate_rules(changed, commit_messages, config, pin_only_paths=pin_only_paths)
    return _report(failures)


if __name__ == "__main__":
    sys.exit(main())
