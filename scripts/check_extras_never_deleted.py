#!/usr/bin/env python3
"""Extras never-delete guard.

Ensures that extras listed in pyproject.toml [project.optional-dependencies]
and uv.lock provides-extras on the base branch are not deleted on HEAD.
An extra may be emptied (name = []) but never deleted while an updater
that names it is still in the field.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_file_at_ref(ref: str, rel_path: str, repo_root: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        capture_output=True, text=True, check=True, cwd=repo_root,
    )
    return result.stdout


def _parse_pyproject_extras(text: str) -> set[str]:
    """Parse [project.optional-dependencies] keys from pyproject.toml text."""
    import tomllib
    import io
    data = tomllib.load(io.BytesIO(text.encode("utf-8")))
    return set(data["project"]["optional-dependencies"])


def _parse_lock_provides_extras(text: str) -> set[str]:
    """Parse provides-extras from uv.lock text."""
    m = re.search(r'^provides-extras = \[(.*?)\]', text, re.M)
    if not m:
        raise ValueError("uv.lock has no provides-extras line")
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def check_extras_never_deleted(
    base_pyproject_text: str,
    head_pyproject_text: str,
    base_lock_text: str,
    head_lock_text: str,
) -> list[str]:
    """Return sorted list of extras deleted between base and head.

    An extra is deleted if it is present in the base text but absent from
    the head text. An empty extra (name = []) still has its key, so it is
    not reported as deleted.
    """
    deleted: list[str] = []

    base_py = _parse_pyproject_extras(base_pyproject_text)
    head_py = _parse_pyproject_extras(head_pyproject_text)
    for extra in sorted(base_py - head_py):
        deleted.append(extra)

    base_lock = _parse_lock_provides_extras(base_lock_text)
    head_lock = _parse_lock_provides_extras(head_lock_text)
    for extra in sorted(base_lock - head_lock):
        if extra not in deleted:
            deleted.append(extra)

    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base ref (e.g. origin/dev)")
    args = parser.parse_args(argv)

    base_pyproject = _read_file_at_ref(args.base, "pyproject.toml")
    head_pyproject = (REPO_ROOT / "pyproject.toml").read_text()

    base_lock = _read_file_at_ref(args.base, "uv.lock")
    head_lock = (REPO_ROOT / "uv.lock").read_text()

    deleted = check_extras_never_deleted(
        base_pyproject, head_pyproject, base_lock, head_lock,
    )

    if deleted:
        print(
            f"EXTRAS NEVER-DELETE FAIL: extras deleted since base: {deleted}. "
            "Empty an extra (name = []) instead of deleting it."
        )
        return 1

    print("check_extras_never_deleted: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
