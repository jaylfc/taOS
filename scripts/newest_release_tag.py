#!/usr/bin/env python3
"""Print the highest version among release tags read from stdin (one per line).

Used by .github/workflows/release.yml to decide whether the tag being released
may be marked "latest": an older tag finishing after a newer one must not
take /releases/latest back. Tags that are not valid PEP 440 versions (historic
one-offs such as v1.0.0-beta.4.1) are skipped rather than crashing the run.
"""
import sys

from packaging.version import InvalidVersion, Version


def newest(tags):
    best = None
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        try:
            v = Version(tag.lstrip("v"))
        except InvalidVersion:
            continue
        if best is None or v > best[0]:
            best = (v, tag)
    return best[1] if best else ""


if __name__ == "__main__":
    print(newest(sys.stdin))
