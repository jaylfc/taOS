"""Tests for scripts/check_schema_column_migrations.py."""
from __future__ import annotations

import ast
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
        schemas = guard_mod._extract_schemas(ast.parse(src))
        assert any("CREATE TABLE foo" in s for s in schemas)

    def test_finds_triple_single_quoted_block(self, guard_mod) -> None:
        src = "SCHEMA = '''\nCREATE TABLE foo (id TEXT);\n'''"
        schemas = guard_mod._extract_schemas(ast.parse(src))
        assert any("CREATE TABLE foo" in s for s in schemas)

    def test_ignores_non_schema_strings(self, guard_mod) -> None:
        src = 'NAME = "CREATE TABLE nope (id TEXT);"'
        schemas = guard_mod._extract_schemas(ast.parse(src))
        assert schemas == []

    def test_ignores_docstring_with_create_table(self, guard_mod) -> None:
        src = '"""Module docstring with CREATE TABLE foo (id TEXT); example."""\nSCHEMA = """\nCREATE TABLE bar (id TEXT);\n"""'
        schemas = guard_mod._extract_schemas(ast.parse(src))
        assert len(schemas) == 1
        assert "CREATE TABLE bar" in schemas[0]

    def test_resolves_module_level_constant(self, guard_mod) -> None:
        src = '''
MY_SCHEMA = """
CREATE TABLE baz (id TEXT);
"""
SCHEMA = MY_SCHEMA
'''
        schemas = guard_mod._extract_schemas(ast.parse(src))
        assert len(schemas) == 1
        assert "CREATE TABLE baz" in schemas[0]

    def test_deduplicates_schema_values(self, guard_mod) -> None:
        src = '''
SCHEMA = """
CREATE TABLE qux (id TEXT);
"""
class Foo:
    SCHEMA = SCHEMA
'''
        schemas = guard_mod._extract_schemas(ast.parse(src))
        assert len(schemas) == 1
        assert "CREATE TABLE qux" in schemas[0]


class TestFindViolations:
    def _patched_baselines(self, guard_mod, monkeypatch, baselines: dict) -> None:
        """Force _baseline_columns to return a fixed baseline per file."""
        def fake(path: Path, ref: str):
            return baselines.get(path.name, {})
        monkeypatch.setattr(guard_mod, "_baseline_columns", fake)

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

class GadgetStore:
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

    def test_comment_with_alter_table_does_not_silence(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
"""Module docstring."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL DEFAULT ''
);
"""
# See ALTER TABLE gadgets ADD COLUMN kind in PR #2416
'''
        path = _write_store(tmp_path, "comment_trick.py", body)
        self._patched_baselines(guard_mod, monkeypatch, {"comment_trick.py": {"gadgets": {"id"}}})
        violations = guard_mod.find_violations(path)
        assert len(violations) == 1
        assert violations[0].column == "kind"

    def test_docstring_with_alter_table_does_not_silence(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
"""Example: ALTER TABLE gadgets ADD COLUMN kind TEXT NOT NULL DEFAULT ''"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL DEFAULT ''
);
"""
'''
        path = _write_store(tmp_path, "docstring_trick.py", body)
        self._patched_baselines(guard_mod, monkeypatch, {"docstring_trick.py": {"gadgets": {"id"}}})
        violations = guard_mod.find_violations(path)
        assert len(violations) == 1
        assert violations[0].column == "kind"

    def test_origin_dev_baseline_error_exits_two(
        self, guard_mod, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS widgets (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL DEFAULT ''
);
"""
'''
        path = _write_store(tmp_path, "error.py", body)
        monkeypatch.setattr(guard_mod, "_baseline_columns", lambda p, ref: None)
        rc = guard_mod.main([str(tmp_path)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "origin/dev" in err


class TestMain:
    def test_passing_fixture_exits_zero(
        self, guard_mod, passing_root: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr(guard_mod, "_baseline_columns", lambda p, ref: _BASELINES.get(p.name, {}))
        monkeypatch.setattr(guard_mod, "_check_base_ref", lambda ref: True)
        rc = guard_mod.main([str(passing_root)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "schema-column-guard: clean" in out

    def test_failing_fixture_exits_one(
        self, guard_mod, failing_root: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        """The failing fixture is the red-side of the contract: a store that
        adds SCHEMA columns with no ALTER must drive main() to exit 1."""
        monkeypatch.setattr(guard_mod, "_baseline_columns", lambda p, ref: _BASELINES.get(p.name, {}))
        monkeypatch.setattr(guard_mod, "_check_base_ref", lambda ref: True)
        rc = guard_mod.main([str(failing_root)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "SCHEMA-COLUMN VIOLATION" in out
        assert "kind" in out
        assert "purpose" in out

    def test_missing_origin_dev_ref_exits_two(
        self, guard_mod, passing_root: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr(guard_mod, "_baseline_columns", lambda p, ref: _BASELINES.get(p.name, {}))
        monkeypatch.setattr(guard_mod, "_check_base_ref", lambda ref: False)
        rc = guard_mod.main([str(passing_root)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "origin/dev" in err


# ---------------------------------------------------------------------------
# Review fold (tsk-kvarzs): regressions for the nine findings on PR #2741.
# ---------------------------------------------------------------------------


class TestBaselineRef:
    """Finding 1: the baseline ref must follow the PR's base branch."""

    def test_base_flag_selects_ref(self, guard_mod, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "stores"
        root.mkdir()
        _write_store(root, "s.py", 'SCHEMA = """\nCREATE TABLE t (id TEXT);\n"""\n')
        seen: list[str] = []

        def fake_check(ref: str) -> bool:
            seen.append(ref)
            return True

        monkeypatch.setattr(guard_mod, "_check_base_ref", fake_check)
        monkeypatch.setattr(guard_mod, "_baseline_columns", lambda p, ref: {"t": {"id"}})
        rc = guard_mod.main(["--base", "origin/master", str(root)])
        assert rc == 0
        assert seen == ["origin/master"]

    def test_bare_branch_name_is_qualified(self, guard_mod, monkeypatch) -> None:
        monkeypatch.delenv("BASE_REF", raising=False)
        monkeypatch.delenv("SCHEMA_COLUMN_BASE_REF", raising=False)
        assert guard_mod._baseline_ref("master") == "origin/master"
        assert guard_mod._baseline_ref("origin/master") == "origin/master"
        assert guard_mod._baseline_ref(None) == "origin/dev"

    def test_base_ref_env_var_is_honoured(self, guard_mod, monkeypatch) -> None:
        monkeypatch.delenv("SCHEMA_COLUMN_BASE_REF", raising=False)
        monkeypatch.setenv("BASE_REF", "master")
        assert guard_mod._baseline_ref(None) == "origin/master"


class TestBaselineLookup:
    """Findings 2 and 8: baseline existence and baseline parsing."""

    def _fake_git(self, guard_mod, monkeypatch, *, exists: bool, show_stdout: str = "") -> None:
        import subprocess as _sp

        def fake_run(cmd, **kwargs):
            if cmd[1] == "cat-file":
                return _sp.CompletedProcess(cmd, 0 if exists else 1, "", "")
            if cmd[1] == "rev-parse":
                return _sp.CompletedProcess(cmd, 0, "deadbeef\n", "")
            if cmd[1] == "show":
                # Deliberately reworded/localized git error text: the guard must
                # not depend on the wording of git's stderr.
                if not exists:
                    return _sp.CompletedProcess(
                        cmd, 128, "", "fatal: Ungueltiges Objekt origin/dev:x.py\n"
                    )
                return _sp.CompletedProcess(cmd, 0, show_stdout, "")
            raise AssertionError(f"unexpected git call: {cmd}")

        monkeypatch.setattr(guard_mod.subprocess, "run", fake_run)

    def test_missing_file_is_new_regardless_of_git_wording(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        path = _write_store(tmp_path, "new_store.py", "SCHEMA = 'CREATE TABLE t (id TEXT);'\n")
        self._fake_git(guard_mod, monkeypatch, exists=False)
        assert guard_mod._baseline_columns(path, "origin/dev") == {}

    def test_baseline_docstring_does_not_add_a_false_column(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        baseline = '''"""Docs: CREATE TABLE gadgets (id TEXT, kind TEXT);"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id TEXT PRIMARY KEY
);
"""
'''
        path = _write_store(
            tmp_path,
            "gadgets.py",
            '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id   TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT ''
);
"""
''',
        )
        self._fake_git(guard_mod, monkeypatch, exists=True, show_stdout=baseline)
        assert guard_mod._baseline_columns(path, "origin/dev") == {"gadgets": {"id"}}
        violations = guard_mod.find_violations(path, "origin/dev")
        assert [v.column for v in violations] == ["kind"]


class TestPostInitDetection:
    """Findings 3 and 4: which ALTER statements may silence a violation."""

    def _baseline(self, guard_mod, monkeypatch, baselines: dict) -> None:
        monkeypatch.setattr(
            guard_mod, "_baseline_columns", lambda p, ref: baselines.get(p.name, {})
        )

    def test_triple_quoted_sql_alter_is_seen(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id   TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT ''
);
"""


class GadgetStore:
    async def _post_init(self) -> None:
        if not await self._has_column("gadgets", "kind"):
            await self._db.execute(
                """
                ALTER TABLE gadgets ADD COLUMN kind TEXT NOT NULL DEFAULT ''
                """
            )
'''
        path = _write_store(tmp_path, "triple.py", body)
        self._baseline(guard_mod, monkeypatch, {"triple.py": {"gadgets": {"id"}}})
        assert guard_mod.find_violations(path, "origin/dev") == []

    def test_hash_inside_sql_literal_does_not_hide_alter(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id   TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT ''
);
"""


class GadgetStore:
    async def _post_init(self) -> None:
        await self._db.execute("-- see issue #2416\\nALTER TABLE gadgets ADD COLUMN kind TEXT")
'''
        path = _write_store(tmp_path, "hashy.py", body)
        self._baseline(guard_mod, monkeypatch, {"hashy.py": {"gadgets": {"id"}}})
        assert guard_mod.find_violations(path, "origin/dev") == []

    def test_module_level_post_init_does_not_silence(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
async def _post_init(store) -> None:
    """Shared helper, not a store method."""
    await store._db.execute("ALTER TABLE gadgets ADD COLUMN kind TEXT")


class GadgetStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS gadgets (
        id   TEXT PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT ''
    );
    """
'''
        path = _write_store(tmp_path, "modlevel.py", body)
        self._baseline(guard_mod, monkeypatch, {"modlevel.py": {"gadgets": {"id"}}})
        violations = guard_mod.find_violations(path, "origin/dev")
        assert [v.column for v in violations] == ["kind"]


class TestSchemaResolution:
    """Findings 5 and 9: resolving SCHEMA values and their aliases."""

    def test_fstring_schema_is_resolved(self, guard_mod) -> None:
        src = 'SCHEMA = f"""\nCREATE TABLE foo (id TEXT);\n"""'
        schemas = guard_mod._extract_schemas(ast.parse(src))
        assert len(schemas) == 1
        assert "CREATE TABLE foo" in schemas[0]

    def test_unresolvable_schema_warns_on_stderr(
        self, guard_mod, capsys: pytest.CaptureFixture
    ) -> None:
        src = "SCHEMA = build_schema()"
        schemas = guard_mod._extract_schemas(ast.parse(src), label="weird.py")
        assert schemas == []
        err = capsys.readouterr().err
        assert "weird.py" in err
        assert "SCHEMA" in err

    def test_class_local_aliases_do_not_collide(self, guard_mod) -> None:
        src = '''
class A:
    SQL = """
    CREATE TABLE alpha (id TEXT);
    """
    SCHEMA = SQL


class B:
    SQL = """
    CREATE TABLE beta (id TEXT);
    """
    SCHEMA = SQL
'''
        schemas = guard_mod._extract_schemas(ast.parse(src))
        joined = "\n".join(schemas)
        assert "CREATE TABLE alpha" in joined
        assert "CREATE TABLE beta" in joined

    def test_function_local_constant_does_not_resolve_class_schema(
        self, guard_mod, capsys: pytest.CaptureFixture
    ) -> None:
        src = '''
def make():
    SQL = """
    CREATE TABLE sneaky (id TEXT);
    """
    return SQL


class Store:
    SCHEMA = SQL
'''
        schemas = guard_mod._extract_schemas(ast.parse(src), label="scoped.py")
        assert schemas == []
        assert "scoped.py" in capsys.readouterr().err


class TestErrorReporting:
    """Findings 6 and 7: broken files fail loudly, violations always print."""

    def test_syntax_error_file_fails_the_gate(
        self, guard_mod, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        root = tmp_path / "stores"
        root.mkdir()
        _write_store(root, "broken.py", 'SCHEMA = """\nCREATE TABLE t (id TEXT);\n')
        monkeypatch.setattr(guard_mod, "_check_base_ref", lambda ref: True)
        monkeypatch.setattr(guard_mod, "_baseline_columns", lambda p, ref: {})
        rc = guard_mod.main([str(root)])
        assert rc == 2
        assert "broken.py" in capsys.readouterr().err

    def test_violations_are_printed_even_when_an_error_occurred(
        self, guard_mod, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        root = tmp_path / "stores"
        root.mkdir()
        _write_store(
            root,
            "bad.py",
            '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id   TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT ''
);
"""
''',
        )
        _write_store(root, "unreadable.py", 'SCHEMA = "CREATE TABLE other (id TEXT);"\n')

        def fake_baseline(path: Path, ref: str):
            if path.name == "unreadable.py":
                return None
            return {"gadgets": {"id"}}

        monkeypatch.setattr(guard_mod, "_check_base_ref", lambda ref: True)
        monkeypatch.setattr(guard_mod, "_baseline_columns", fake_baseline)
        rc = guard_mod.main([str(root)])
        captured = capsys.readouterr()
        assert rc == 2
        assert "SCHEMA-COLUMN VIOLATION" in captured.out
        assert "kind" in captured.out


class TestNewTables:
    """A table absent from the baseline is created in full on every install.

    ``CREATE TABLE IF NOT EXISTS`` builds a brand-new table with all its
    columns, so no ALTER applies and there is nothing to brick. Diffing such a
    table against an empty baseline made every column look newly added and
    produced one violation per column.
    """

    def _baseline(self, guard_mod, monkeypatch, baselines: dict) -> None:
        monkeypatch.setattr(
            guard_mod, "_baseline_columns", lambda p, ref: baselines.get(p.name, {})
        )

    def test_new_store_file_with_empty_baseline_is_clean(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS widgets (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL DEFAULT '',
    created INTEGER NOT NULL DEFAULT 0
);
"""
'''
        path = _write_store(tmp_path, "brand_new_store.py", body)
        # A file that does not exist on the baseline yields {}.
        self._baseline(guard_mod, monkeypatch, {"brand_new_store.py": {}})
        assert guard_mod.find_violations(path, "origin/dev") == []

    def test_second_table_added_to_existing_file_is_clean(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id   TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS gadget_tags (
    gadget_id TEXT NOT NULL,
    tag       TEXT NOT NULL
);
"""
'''
        path = _write_store(tmp_path, "two_tables.py", body)
        self._baseline(
            guard_mod, monkeypatch, {"two_tables.py": {"gadgets": {"id", "kind"}}}
        )
        assert guard_mod.find_violations(path, "origin/dev") == []

    def test_new_table_does_not_mask_a_new_column_on_an_existing_table(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        """Skipping new tables must not weaken the check on the old ones."""
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS gadgets (
    id   TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS gadget_tags (
    gadget_id TEXT NOT NULL,
    tag       TEXT NOT NULL
);
"""
'''
        path = _write_store(tmp_path, "mixed.py", body)
        self._baseline(guard_mod, monkeypatch, {"mixed.py": {"gadgets": {"id"}}})
        violations = guard_mod.find_violations(path, "origin/dev")
        assert [(v.table, v.column) for v in violations] == [("gadgets", "kind")]

    def test_new_store_file_exits_zero_end_to_end(
        self, guard_mod, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        root = tmp_path / "stores"
        root.mkdir()
        _write_store(
            root,
            "fresh.py",
            '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS fresh_rows (
    id    TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""
''',
        )
        monkeypatch.setattr(guard_mod, "_check_base_ref", lambda ref: True)
        monkeypatch.setattr(guard_mod, "_baseline_columns", lambda p, ref: {})
        rc = guard_mod.main([str(root)])
        assert rc == 0
        assert "schema-column-guard: clean" in capsys.readouterr().out


class TestSqlComments:
    """An inline ``--`` comment used to hide every column after the first.

    The comment runs to the end of the line, including the comma that ends the
    column it documents, so the next segment started with ``--`` and failed the
    column-name match. Any store that documents its columns inline -- the house
    style in `contacts_store.py` -- was almost entirely invisible to the guard.
    """

    def test_line_comments_do_not_hide_following_columns(self, guard_mod) -> None:
        body = (
            "contact_id   TEXT PRIMARY KEY,  -- canonical key\n"
            "hub_username TEXT NOT NULL,     -- display column\n"
            "status       TEXT NOT NULL DEFAULT 'pending',  -- pending|active\n"
            "local_crm_id TEXT,              -- optional link\n"
            "note         TEXT\n"
        )
        assert guard_mod._split_columns(body) == {
            "contact_id", "hub_username", "status", "local_crm_id", "note",
        }

    def test_double_dash_inside_a_literal_is_not_a_comment(self, guard_mod) -> None:
        body = "id TEXT, sep TEXT NOT NULL DEFAULT '-- not a comment', tail TEXT"
        assert guard_mod._split_columns(body) == {"id", "sep", "tail"}

    def test_block_comment_is_removed(self, guard_mod) -> None:
        body = "id TEXT, /* note, with a comma */ tail TEXT"
        assert guard_mod._split_columns(body) == {"id", "tail"}

    def test_commented_column_add_is_still_caught(
        self, guard_mod, tmp_path: Path, monkeypatch
    ) -> None:
        """The end-to-end shape: a documented store gaining a documented column."""
        body = '''
SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id   TEXT PRIMARY KEY,  -- canonical key
    hub_username TEXT NOT NULL,     -- display column
    trust_level  TEXT NOT NULL DEFAULT 'none'  -- added by this change
);
"""
'''
        path = _write_store(tmp_path, "documented.py", body)
        monkeypatch.setattr(
            guard_mod,
            "_baseline_columns",
            lambda p, ref: {"contacts": {"contact_id", "hub_username"}},
        )
        violations = guard_mod.find_violations(path, "origin/dev")
        assert [(v.table, v.column) for v in violations] == [("contacts", "trust_level")]
