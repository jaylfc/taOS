"""Existing-install upgrade: a data dir written by taosmd 0.4.0, run under the pinned taosmd.

The fixture in ``tests/fixtures/taosmd_040_store`` was written by a real
taosmd 0.4.0 (see ``make_fixture.py`` there). These tests open it with whatever
taosmd is installed, which must be the version ``pyproject.toml`` pins, and drive
the real ``python -m taosmd migrate --probe`` CLI and the real store classes the
way ``tinyagentos/app.py`` constructs them. Nothing is faked.

What 0.5.x does with a 0.4.0 store (observed, asserted below):

* The read-only probe exits 10 ("old"): a supported state, served as is.
* Opening the stores applies the pending schema steps (``user_version`` is
  stamped) and keeps every row; nothing is lost and nothing is refused.
* Dense vectors stay JSON text. The float32 conversion is a separate, explicit,
  one-way command and never runs on open.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import taosmd

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "taosmd_040_store"
STORE_FILES = ("archive-index.db", "knowledge-graph.db", "vector-memory.db")


def _pinned_version() -> str:
    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    for dep in deps:
        m = re.match(r"taosmd(\[[^\]]*\])?==([\w.]+)$", dep)
        if m:
            return m.group(2)
    raise AssertionError("pyproject.toml does not pin taosmd with ==")


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _probe(data_dir: Path) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-m", "taosmd", "--data-dir", str(data_dir), "migrate", "--probe", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def store_040(tmp_path: Path) -> Path:
    dest = tmp_path / "data"
    shutil.copytree(FIXTURE, dest, ignore=shutil.ignore_patterns("make_fixture.py"))
    return dest


def test_installed_taosmd_is_the_pinned_version():
    assert taosmd.__version__ == _pinned_version()


def test_fixture_really_is_a_040_store(store_040: Path):
    """Guard the fixture: it must carry the pre-0.5 on-disk state, not a migrated one."""
    for name in STORE_FILES:
        conn = sqlite3.connect(f"file:{store_040 / name}?mode=ro&immutable=1", uri=True)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 0, name
        finally:
            conn.close()


def test_probe_reports_old_and_changes_nothing(store_040: Path):
    before = _digest(store_040)
    code, report = _probe(store_040)
    assert code == 10, report
    assert report["state"] == "old"
    states = {d["db"]: d["state"] for d in report["databases"]}
    assert states["vector_memory"] == "old"
    assert "unknown" not in states.values() and "newer" not in states.values()
    assert _digest(store_040) == before  # read-only: no byte changed, no sidecar created


@pytest.mark.asyncio
async def test_taos_stores_open_a_040_store_and_keep_every_row(store_040: Path):
    """Open the stores exactly as tinyagentos/app.py does; all 0.4.0 data is still there."""
    from taosmd import Archive as ArchiveStore
    from taosmd import KnowledgeGraph as TemporalKnowledgeGraph
    from taosmd.vector_memory import VectorMemory

    kg = TemporalKnowledgeGraph(db_path=store_040 / "knowledge-graph.db")
    await kg.init()
    try:
        triples = await kg.query_entity("Jay")
        assert any(t.get("object_id") == "tea" for t in triples), triples
    finally:
        await kg.close()

    archive = ArchiveStore(archive_dir=store_040 / "archive", index_path=store_040 / "archive-index.db")
    await archive.init()
    try:
        assert await archive.count() == 1
        events = await archive.query(limit=10)
        assert [e["summary"] for e in events] == ["hello"]
    finally:
        await archive.close()

    vm = VectorMemory(db_path=store_040 / "vector-memory.db")
    await vm.init()
    try:
        assert await vm.count() == 1
    finally:
        await vm.close()

    # Opening applied the schema steps and stamped the versions, but did not
    # convert the vectors: they are still JSON text and the probe still says old.
    conn = sqlite3.connect(store_040 / "vector-memory.db")
    try:
        emb = conn.execute("SELECT embedding FROM vector_memory").fetchone()[0]
    finally:
        conn.close()
    assert emb.startswith("[")
    code, report = _probe(store_040)
    assert code == 10, report
    stamped = [d for d in report["databases"] if d["state"] != "absent"]
    assert stamped and all(d["user_version"] == d["latest"] for d in stamped), stamped


@pytest.mark.asyncio
async def test_taos_boots_on_a_040_store_and_keeps_the_rows(store_040: Path):
    """The real application lifespan over a data dir a 0.4.0 wrote."""
    import yaml

    from tinyagentos.app import create_app

    (store_040 / "config.yaml").write_text(yaml.dump({
        "server": {"host": "127.0.0.1", "port": 6969}, "backends": [], "qmd": {"url": "http://localhost:7832"},
        "agents": [], "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (store_040 / ".setup_complete").touch()
    app = create_app(data_dir=store_040)
    async with app.router.lifespan_context(app):
        assert await app.state.archive.count() == 1
    code, report = _probe(store_040)
    assert code == 10, report  # still a supported old store: vectors are JSON until the owner converts them
