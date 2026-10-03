"""Guard: fail if any file under tests/ or tinyagentos/ still references the
dead ``TINYAGENTOS_DATA_DIR`` environment variable.

The application reads ``TAOS_DATA_DIR``, not ``TINYAGENTOS_DATA_DIR`` (see
``tinyagentos/app.py``).  A previous generation of tests set the wrong name,
which meant ``create_app()`` fell back to ``PROJECT_DIR / "data"`` and those
tests mutated the repository's live data folder.  This test is the permanent
guard: if the old name reappears anywhere in the codebase, CI fails here before
any test runs.
"""
from __future__ import annotations

import pathlib

import pytest

PROJECT_DIR = pathlib.Path(__file__).resolve().parent.parent
_BAD_ENV = "TINYAGENTOS_DATA_DIR"


def _scan_for_dead_env(root: pathlib.Path) -> list[pathlib.Path]:
    hits: list[pathlib.Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".py", ".yaml", ".yml", ".toml", ".json", ".md", ".sh"):
            continue
        if path.resolve() == pathlib.Path(__file__).resolve():
            continue
        if path.name == "conftest.py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _BAD_ENV in text:
            hits.append(path)
    return hits


class TestNoDeadDataDirEnv:
    def test_tinyagentos_data_dir_is_not_referenced(self):
        hits = _scan_for_dead_env(PROJECT_DIR / "tests")
        hits += [p for p in _scan_for_dead_env(PROJECT_DIR / "tinyagentos") if p not in hits]
        assert not hits, (
            f"The dead env name {_BAD_ENV!r} is still referenced in: "
            f"{', '.join(str(p.relative_to(PROJECT_DIR)) for p in hits)}. "
            f"Use TAOS_DATA_DIR or pass data_dir= to create_app() instead."
        )
