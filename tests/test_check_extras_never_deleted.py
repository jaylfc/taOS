"""Unit tests for scripts/check_extras_never_deleted.py.

These call check_extras_never_deleted() directly with synthetic inputs --
no shelling out to git, no dependence on the state of this checkout's
actual history.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_extras_never_deleted as cend  # noqa: E402

check_extras_never_deleted = cend.check_extras_never_deleted


# Minimal valid pyproject.toml snippets
PYPROJECT_BASE = """\
[project]
name = "test"

[project.optional-dependencies]
a = ["foo"]
b = ["bar"]
"""

PYPROJECT_HEAD_EMPTY_B = """\
[project]
name = "test"

[project.optional-dependencies]
a = []
b = []
"""

PYPROJECT_HEAD_MISSING_B = """\
[project]
name = "test"

[project.optional-dependencies]
a = ["foo"]
"""

PYPROJECT_HEAD_ADDED_C = """\
[project]
name = "test"

[project.optional-dependencies]
a = ["foo"]
b = ["bar"]
c = ["baz"]
"""

# Minimal valid uv.lock snippets
LOCK_BASE = """\
version = 1
[[package]]
name = "foo"
version = "1.0.0"

[project]
name = "test"
requires-python = ">=3.11"

[package.metadata]
provides-extras = ["a", "b"]
"""

LOCK_HEAD_MISSING_B = """\
version = 1
[[package]]
name = "foo"
version = "1.0.0"

[project]
name = "test"
requires-python = ">=3.11"

[package.metadata]
provides-extras = ["a"]
"""

LOCK_HEAD_EMPTY_B = """\
version = 1
[[package]]
name = "foo"
version = "1.0.0"

[project]
name = "test"
requires-python = ">=3.11"

[package.metadata]
provides-extras = ["a", "b"]
"""

LOCK_HEAD_ADDED_C = """\
version = 1
[[package]]
name = "foo"
version = "1.0.0"

[project]
name = "test"
requires-python = ">=3.11"

[package.metadata]
provides-extras = ["a", "b", "c"]
"""


def test_deleted_pyproject_extra_fails():
    deleted = check_extras_never_deleted(
        PYPROJECT_BASE, PYPROJECT_HEAD_MISSING_B, LOCK_BASE, LOCK_BASE,
    )
    assert "b" in deleted


def test_deleted_lock_extra_fails():
    deleted = check_extras_never_deleted(
        PYPROJECT_BASE, PYPROJECT_BASE, LOCK_BASE, LOCK_HEAD_MISSING_B,
    )
    assert "b" in deleted


def test_emptied_pyproject_extra_passes():
    deleted = check_extras_never_deleted(
        PYPROJECT_BASE, PYPROJECT_HEAD_EMPTY_B, LOCK_BASE, LOCK_BASE,
    )
    assert not deleted


def test_added_extra_passes():
    deleted = check_extras_never_deleted(
        PYPROJECT_BASE, PYPROJECT_HEAD_ADDED_C, LOCK_BASE, LOCK_HEAD_ADDED_C,
    )
    assert not deleted


def test_deleted_from_both_sources_reported_once():
    deleted = check_extras_never_deleted(
        PYPROJECT_BASE, PYPROJECT_HEAD_MISSING_B, LOCK_BASE, LOCK_HEAD_MISSING_B,
    )
    assert deleted == ["b"]
