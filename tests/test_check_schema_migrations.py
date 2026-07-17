"""Tests for the schema-migration-guard static check (taOS #1865)."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# scripts/ is not a package; make it importable the same way the other
# scripts/*.py unit tests do (see tests/test_doc_gate.py).
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_schema_migrations as csm  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_tmp_store(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "synthetic_store.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_real_tree_is_clean() -> None:
    violations = csm.find_all_violations(REPO_ROOT / "tinyagentos")
    assert violations == [], [str(v) for v in violations]


def test_flags_violating_store(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class BadStore(BaseStore):
            SCHEMA = """
            CREATE TABLE t (id INTEGER);
            CREATE INDEX ix ON t(newcol);
            """

            async def _post_init(self):
                await self._db.execute("ALTER TABLE t ADD COLUMN newcol TEXT")
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = csm.find_violations(path)
    assert len(violations) == 1
    v = violations[0]
    assert v.table == "t"
    assert v.column == "newcol"


def test_safe_store_not_flagged(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class SafeStore(BaseStore):
            SCHEMA = """
            CREATE TABLE t (id INTEGER, goodcol TEXT);
            CREATE INDEX ix ON t(goodcol);
            """

            async def _post_init(self):
                # unrelated migration column; index is on a declared column.
                await self._db.execute("ALTER TABLE t ADD COLUMN othercol TEXT")
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    assert csm.find_violations(path) == []


def test_constant_schemas_resolved(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        SOME_CONST = """
        CREATE TABLE u (id INTEGER);
        CREATE UNIQUE INDEX ux ON u(extra);
        """

        class ConstStore(BaseStore):
            SCHEMA = SOME_CONST

            async def _post_init(self):
                await self._db.execute("ALTER TABLE u ADD COLUMN extra TEXT")
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = csm.find_violations(path)
    assert len(violations) == 1
    assert violations[0].table == "u"
    assert violations[0].column == "extra"
