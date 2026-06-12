#!/usr/bin/env python3
"""Compile docs/agent-manual/ source files into docs/taos-agent-manual.md.

Usage:
    python3 scripts/build-agent-manual.py               # writes to docs/taos-agent-manual.md
    python3 scripts/build-agent-manual.py --output PATH # writes to PATH (used by tests)

The script is deterministic and idempotent: running it twice produces identical output.
"""

import argparse
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "docs" / "agent-manual"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "taos-agent-manual.md"

HEADER = (
    "<!-- GENERATED from docs/agent-manual/ by scripts/build-agent-manual.py."
    " Edit the source files, not this file. -->\n"
)

SEPARATOR = "\n---\n\n"


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of more than one blank line into a single blank line."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _strip_trailing_whitespace(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines)


def _strip_purpose_comment(text: str) -> str:
    """Remove the one-line HTML purpose comment from each source file."""
    return re.sub(r"^<!-- .+ -->\n", "", text, flags=re.MULTILINE)


def build(output_path: pathlib.Path = DEFAULT_OUTPUT) -> str:
    source_files = sorted(SOURCE_DIR.glob("[0-9]*.md"))
    if not source_files:
        print(f"ERROR: no numbered source files found in {SOURCE_DIR}", file=sys.stderr)
        sys.exit(1)

    sections = []
    for path in source_files:
        raw = path.read_text(encoding="utf-8")
        cleaned = _strip_purpose_comment(raw).strip()
        sections.append(cleaned)

    body = SEPARATOR.join(sections)
    output = HEADER + "\n" + body + "\n"
    output = _collapse_blank_lines(output)
    output = _strip_trailing_whitespace(output)
    # Ensure exactly one trailing newline
    output = output.rstrip("\n") + "\n"

    output_path.write_text(output, encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile the taOS agent manual.")
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result = build(output_path=args.output)
    print(f"Built {args.output} ({len(result)} chars, {len(result.splitlines())} lines)")
