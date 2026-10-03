#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one released version.

Used by .github/workflows/release.yml to build the GitHub Release body from
the section the release commit collated, so a pushed tag always becomes a
published release with the same notes the CHANGELOG carries.

    python3 scripts/changelog_section.py 1.0.0-beta.56 [CHANGELOG.md]

Prints the body between the `## [<version>]` heading (any trailing text, usually
`- <date>`, is allowed) and the next `## [` heading.
Exits non-zero when the section is missing or empty: a release must never be
published with notes from the wrong version or no notes at all.
"""
import re
import sys
from pathlib import Path


def extract_section(text: str, version: str) -> str:
    heading = re.compile(r"^## \[" + re.escape(version) + r"\](?:\s|$)")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise LookupError(f"no '## [{version}]' section in CHANGELOG")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## [")),
        len(lines),
    )
    body = "\n".join(lines[start + 1:end]).strip()
    if not body:
        raise LookupError(f"'## [{version}]' section is empty")
    return body + "\n"


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: changelog_section.py <version> [CHANGELOG.md]", file=sys.stderr)
        return 2
    version = argv[1].removeprefix("v")
    path = Path(argv[2]) if len(argv) == 3 else Path("CHANGELOG.md")
    try:
        sys.stdout.write(extract_section(path.read_text(encoding="utf-8"), version))
    except (LookupError, OSError) as exc:
        print(f"changelog_section: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
