"""Tests for scripts/check_schema_column_migrations.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_schema_column_migrations.py"


def _load_module():
    import sys
    spec = importlib.util.spec_from_file_location(
        "check_schema_column_migrations", _SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard_mod():
    return _load_module()


def _write_store(tmp_path: Path, name: str, body: str) -> Path:
    """Write a synthetic store file under tmp_path/<name>. Returns the path."""
    p = tmp_path / name
    p.write_text(body)
    return p


# Module-level fixtures used by TestMain. Each test builds its own root so
# the passing and failing fixtures stay independent.
@pytest.fixture
def passing_root(tmp_path: Path) -> Path:
    """A stores root with one store whose columns are all pre-existing."""
    root = tmp_path / "stores_passing"
    root.mkdir()
    _write_store(
        root,
        "good_store.py",
        '''
"""All columns already exist on origin/dev, no new ones -> clean."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS widgets (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL DEFAULT ''
);
"""
''',
    )
    return root


@pytest.fixture
def failing_root(tmp_path: Path) -> Path:
    """A stores root with one store that adds SCHEMA columns with no ALTER."""
    root = tmp_path / "stores_failing"
    root.mkdir()
    _write_store(
        root,
        "bad_store.py",
        '''
"""Adds two new columns directly into CREATE TABLE with no ALTER -> red."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT ''
);
"""
''',
    )
    return root


# Baseline shape per file (table -> columns) used to simulate
# `git show origin/dev:<path>` so the guard does not shell out.
_BASELINES = {
    "good_store.py": {"widgets": {"id", "name"}},
    "bad_store.py": {"gadgets": {"id"}},
}


class TestSplitColumns:
    def test_extracts_simple_columns(self, guard_mod) -> None:
        body = "id TEXT PRIMARY KEY,\n    name TEXT NOT NULL DEFAULT ''\n"
        assert guard_mod._split_columns(body) == {"id", "name"}

    def test_skips_inline_constraints(self, guard_mod) -> None:
        body = (
            "id INTEGER PRIMARY KEY,\n"
            "name TEXT,\n"
            "UNIQUE (name),\n"
            "CHECK (id > 0)\n"
        )
        assert guard_mod._split_columns(body) == {"id", "name"}

    def test_handles_commas_inside_parentheses(self, guard_mod) -> None:
        body = (
            "id INTEGER PRIMARY KEY,\n"
            "name TEXT NOT NULL DEFAULT func(a, b, c)\n"
        )
        assert guard_mod._split_columns(body) == {"id", "name"}


class TestExtractSchemas:
    def test_finds_triple_double_quoted_block(self, guard_mod) -> None:
        src = 'SCHEMA = """\nCREATE TABLE foo (id TEXT);\n"""'
        schemas = guard_mod._extract_schemas(src)
        assert any("CREATE TABLE foo" in s for s in schemas)

    def test_finds_triple_single_quoted_block(self, guard_mod) -> None:
        src = "SCHEMA = '''\nCREATE TABLE foo (id TEXT);\n'''"
        schemas = guard_mod._extract_schemas(src)
        assert any("CREATE TABLE foo" in s for s in schemas)

    def test_ignores_non_schema_strings(self, guard_mod) -> None:
        src = 'NAME = "CREATE TABLE nope (id TEXT);"'
        assert guard_mod._extract_schemas(src) == []


class TestFindViolations:
    def _patched_baselines(self, guard_mod, monkeypatch, baselines: dict) -> None:
        """Force _origin_dev_columns to return a fixed baseline per file."""
        def fake(path: Path):
            return baselines.get(path.name, {})
        monkeypatch.setattr(guard_mod, "_origin_dev_columns", fake)

    def test_passing_fixture_no_violations(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS widgets (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL DEFAULT ''
);
"""
'''
        path = _write_store(tmp_path, "ok.py", body)
        self._patched_baselines(guard_mod, monkeypatch, {"ok.py": {"widgets": {"id", "name"}}})
        assert guard_mod.find_violations(path) == []

    def test_failing_fixture_emits_one_violation_per_new_column(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT ''
);
"""
'''
        path = _write_store(tmp_path, "broken.py", body)
        self._patched_baselines(guard_mod, monkeypatch, {"broken.py": {"gadgets": {"id"}}})
        violations = guard_mod.find_violations(path)
        assert {v.column for v in violations} == {"kind", "purpose"}
        assert all(v.table == "gadgets" for v in violations)

    def test_alter_for_new_column_silences_violation(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id   TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT ''
);
"""

async def _post_init(self):
    if not await self._has_column("gadgets", "kind"):
        await self._db.execute("ALTER TABLE gadgets ADD COLUMN kind TEXT NOT NULL DEFAULT ''")
'''
        path = _write_store(tmp_path, "migrated.py", body)
        self._patched_baselines(guard_mod, monkeypatch, {"migrated.py": {"gadgets": {"id"}}})
        assert guard_mod.find_violations(path) == []

    def test_pre_existing_column_with_alter_still_clean(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS widgets (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT ''
);
"""
'''
        path = _write_store(tmp_path, "stable.py", body)
        # Baseline already has both columns -> nothing is "new".
        self._patched_baselines(
            guard_mod, monkeypatch, {"stable.py": {"widgets": {"id", "name"}}}
        )
        assert guard_mod.find_violations(path) == []


class TestMain:
    def test_passing_fixture_exits_zero(
        self, guard_mod, passing_root: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr(guard_mod, "_origin_dev_columns", lambda p: _BASELINES.get(p.name, {}))
        rc = guard_mod.main([str(passing_root)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "schema-column-guard: clean" in out

    def test_failing_fixture_exits_one(
        self, guard_mod, failing_root: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        """The failing fixture is the red-side of the contract: a store that
        adds SCHEMA columns with no ALTER must drive main() to exit 1."""
        monkeypatch.setattr(guard_mod, "_origin_dev_columns", lambda p: _BASELINES.get(p.name, {}))
        rc = guard_mod.main([str(failing_root)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "SCHEMA-COLUMN VIOLATION" in out
        assert "kind" in out
        assert "purpose" in out