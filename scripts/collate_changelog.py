#!/usr/bin/env python3
"""Fold changelog.d/*.md fragments into a new CHANGELOG.md version section.

Run from the release bump, before tagging:

    python3 scripts/collate_changelog.py 1.0.0-beta.47

Reads every ``changelog.d/*.md``, groups the bullets under their declared
``### Section`` heading (defaulting to ``### Added``), writes them as a new
``## [<version>] - <today>`` section directly beneath ``## [Unreleased]`` in
CHANGELOG.md, and deletes the fragments it consumed.

Fragments exist because every PR editing CHANGELOG.md writes at the same anchor,
so concurrent PRs conflict there by construction. A fragment is a new file with a
unique name and cannot conflict. See docs/changelog-fragments.md.

The convention doc deliberately lives OUTSIDE changelog.d/: a README.md in there
would match the doc-gate's ``changelog.d/*.md`` glob and let a PR satisfy the
changelog rule by touching the readme.

Exit codes: 0 on success (including "no fragments, nothing to do"), 1 on a
malformed fragment or a CHANGELOG.md missing its [Unreleased] anchor.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"
DEFAULT_SECTION = "### Added"
# Keep-a-Changelog order, so a release section always reads the same way
# regardless of the order the fragments happened to land in.
SECTION_ORDER = ["### Added", "### Changed", "### Fixed", "### Removed", "### Security"]


def parse_fragment(path: Path) -> dict[str, list[str]]:
    """Return {section: [bullet lines]} for one fragment file."""
    sections: dict[str, list[str]] = {}
    current = DEFAULT_SECTION
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            current = line.strip()
            continue
        # Continuation lines of a wrapped bullet are indented; they stay with
        # the bullet rather than becoming a new entry.
        sections.setdefault(current, []).append(line)
    if not sections:
        raise ValueError(f"{path.name} contains no changelog bullets")
    return sections


def collect(fragment_dir: Path) -> tuple[dict[str, list[str]], list[Path]]:
    merged: dict[str, list[str]] = {}
    consumed: list[Path] = []
    # Sorted so the output is deterministic: same fragments, same section order.
    for path in sorted(fragment_dir.glob("*.md")):
        for section, lines in parse_fragment(path).items():
            merged.setdefault(section, []).extend(lines)
        consumed.append(path)
    return merged, consumed


def render(version: str, merged: dict[str, list[str]], today: str) -> str:
    out = [f"## [{version}] - {today}", ""]
    ordered = [s for s in SECTION_ORDER if s in merged]
    ordered += [s for s in sorted(merged) if s not in SECTION_ORDER]
    for section in ordered:
        out.append(section)
        out.append("")
        out.extend(merged[section])
        out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Fold changelog fragments into CHANGELOG.md")
    ap.add_argument("version", help="release version, e.g. 1.0.0-beta.47")
    ap.add_argument("--date", default=None, help="release date (YYYY-MM-DD), defaults to today")
    ap.add_argument("--dry-run", action="store_true", help="print the section, change nothing")
    args = ap.parse_args(argv)

    if not FRAGMENT_DIR.is_dir():
        print("collate-changelog: no changelog.d/ directory, nothing to do")
        return 0
    merged, consumed = collect(FRAGMENT_DIR)
    if not consumed:
        print("collate-changelog: no fragments, nothing to do")
        return 0

    today = args.date or datetime.date.today().isoformat()
    section = render(args.version, merged, today)

    if args.dry_run:
        print(section)
        return 0

    text = CHANGELOG.read_text(encoding="utf-8")
    if UNRELEASED not in text:
        print(f"collate-changelog: {CHANGELOG.name} has no '{UNRELEASED}' anchor", file=sys.stderr)
        return 1

    version_header = f"## [{args.version}]"
    if version_header in text:
        # Rerun after a partial failure: only consume fragments whose content
        # already made it into the changelog. A fragment that landed AFTER the
        # failed run is folded nowhere -- unlinking it would silently lose its
        # release note, so keep it and refuse loudly instead.
        print(f"collate-changelog: {args.version} section already present in {CHANGELOG.name}, consuming folded leftover fragments", file=sys.stderr)
        # Match against ONLY the target version's section. A bullet that happens
        # to also appear under an OLDER release must not count as folded --
        # unlinking on a whole-file match would silently lose the new note,
        # which is the exact class this branch exists to prevent.
        start = text.index(version_header)
        next_header = text.find("\n## [", start + len(version_header))
        section_text = text[start:] if next_header == -1 else text[start:next_header]
        unfolded: list[Path] = []
        for path in consumed:
            lines = [ln for section_lines in parse_fragment(path).values() for ln in section_lines]
            if all(ln in section_text for ln in lines):
                path.unlink()
            else:
                unfolded.append(path)
        print(f"collate-changelog: consumed {len(consumed) - len(unfolded)} leftover fragment(s) for {args.version}")
        if unfolded:
            for path in unfolded:
                print(f"collate-changelog: {path.name} is not folded into {CHANGELOG.name} -- kept; rerun with the correct target version", file=sys.stderr)
            return 1
        return 0

    # Insert directly BELOW [Unreleased] so Unreleased stays empty and on top,
    # which is what the release train expects on the next cycle.
    anchor = UNRELEASED + "\n"
    text = text.replace(anchor, anchor + "\n" + section, 1)
    CHANGELOG.write_text(text, encoding="utf-8")

    for path in consumed:
        path.unlink()
    print(f"collate-changelog: folded {len(consumed)} fragment(s) into {CHANGELOG.name} as {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
