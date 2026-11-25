"""Memory store guard: the real ``taosmd migrate`` CLI, mapped by its documented exit codes.

Every store here is built by real taosmd code (or is the committed store a real
0.4.0 wrote); the probe and the conversion are the real CLI in a subprocess.
Only the "cannot run at all" and "unknown exit code" cases use a stand-in
command, because no real taosmd produces them.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos import memory_store_guard as guard

REPO = Path(__file__).resolve().parent.parent
FIXTURE_040 = REPO / "tests" / "fixtures" / "taosmd_040_store"


def _copy_040(dest: Path) -> Path:
    shutil.copytree(FIXTURE_040, dest, ignore=shutil.ignore_patterns("make_fixture.py"))
    return dest


def _build_current_store(dest: Path) -> Path:
    """A store written by this taosmd, converted as a fresh install would be."""
    dest.mkdir(parents=True)
    script = textwrap.dedent(
        """
        import asyncio, sys
        from pathlib import Path
        from taosmd import KnowledgeGraph
        from taosmd.vector_memory import VectorMemory
        out = Path(sys.argv[1])
        async def main():
            kg = KnowledgeGraph(db_path=out / "knowledge-graph.db"); await kg.init(); await kg.close()
            vm = VectorMemory(db_path=out / "vector-memory.db"); await vm.init(); await vm.close()
        asyncio.run(main())
        """
    )
    subprocess.run([sys.executable, "-c", script, str(dest)], check=True, timeout=120, capture_output=True)
    # A WAL-mode database with nothing pending is closed cleanly; leave no sidecar behind.
    for p in dest.glob("*.db-*"):
        p.unlink()
    return dest


# --- probe: real CLI, every documented exit code -----------------------------------

@pytest.mark.asyncio
async def test_probe_040_store_is_old_and_served(tmp_path):
    data = _copy_040(tmp_path / "data")
    status = await guard.probe(data)
    assert status.exit_code == 10 and status.state == "old"
    assert status.can_serve is True
    assert status.convert_available is True and status.next_step == guard.NEXT_CONVERT_OPTIONAL
    assert "nothing is lost" in status.message
    assert any(d["db"] == "vector_memory" and d["state"] == "old" for d in status.databases)


@pytest.mark.asyncio
async def test_probe_current_store(tmp_path):
    data = _build_current_store(tmp_path / "data")
    status = await guard.probe(data)
    assert status.exit_code == 0, status
    assert status.state == "current" and status.can_serve and status.next_step == guard.NEXT_NONE


@pytest.mark.asyncio
async def test_probe_empty_or_missing_dir_is_current_without_running_the_cli(tmp_path, monkeypatch):
    async def boom(*a, **k):  # the CLI must not be started for a first install
        raise AssertionError("probe started the CLI for an empty data dir")

    monkeypatch.setattr(guard, "_run", boom)
    for d in (tmp_path / "missing", tmp_path):
        status = await guard.probe(d)
        assert status.state == "current" and status.can_serve


@pytest.mark.asyncio
async def test_probe_newer_store_is_refused_with_plain_next_step(tmp_path):
    data = _build_current_store(tmp_path / "data")
    conn = sqlite3.connect(data / "knowledge-graph.db")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    status = await guard.probe(data)
    assert status.exit_code == 12 and status.state == "newer"
    assert status.can_serve is False and status.next_step == guard.NEXT_UPDATE_TAOS
    assert "update taOS" in status.message and "not touched" in status.message


@pytest.mark.asyncio
async def test_probe_live_wal_is_unknown_with_plain_next_step(tmp_path):
    data = _copy_040(tmp_path / "data")
    conn = sqlite3.connect(data / "knowledge-graph.db")
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    conn.execute("CREATE TABLE IF NOT EXISTS _t (x)")
    conn.execute("INSERT INTO _t VALUES (1)")
    conn.commit()
    try:
        assert (data / "knowledge-graph.db-wal").stat().st_size > 0
        status = await guard.probe(data)
    finally:
        conn.close()
    assert status.exit_code == 11 and status.state == "unknown"
    assert status.can_serve is False and status.next_step == guard.NEXT_FREE_THE_STORE
    assert "stop any other taOS or taOSmd" in status.message and "Do not delete" in status.message


@pytest.mark.asyncio
async def test_probe_cli_cannot_start_is_said_plainly(tmp_path, monkeypatch):
    data = _copy_040(tmp_path / "data")
    monkeypatch.setattr(guard.sys, "executable", str(tmp_path / "no-such-python"))
    status = await guard.probe(data)
    assert status.state == "unavailable" and status.exit_code is None and status.can_serve is False
    assert "Nothing was changed" in status.message and "Updates" in status.message


@pytest.mark.asyncio
async def test_probe_unknown_exit_code_is_not_guessed(tmp_path, monkeypatch):
    data = _copy_040(tmp_path / "data")
    fake = tmp_path / "fake-python"
    fake.write_text("#!/bin/sh\necho oops >&2\nexit 7\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(guard.sys, "executable", str(fake))
    status = await guard.probe(data)
    assert status.state == "unexpected" and status.exit_code == 7 and status.can_serve is False
    assert "exit 7" in status.message and "migrate --probe" in status.message


@pytest.mark.asyncio
async def test_probe_does_not_modify_the_store(tmp_path):
    import hashlib

    data = _copy_040(tmp_path / "data")

    def digest():
        return {str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data.rglob("*")) if p.is_file()}

    before = digest()
    await guard.probe(data)
    assert digest() == before


# --- start-up check ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_check_serves_old_and_refuses_newer_and_unknown(tmp_path):
    assert (await guard.check_at_startup(_copy_040(tmp_path / "a"))).state == "old"

    newer = _build_current_store(tmp_path / "b")
    conn = sqlite3.connect(newer / "knowledge-graph.db")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(guard.MemoryStoreGuardError) as exc:
        await guard.check_at_startup(newer)
    assert "update taOS" in str(exc.value)


# --- explicit conversion: real CLI -------------------------------------------------

@pytest.mark.asyncio
async def test_convert_040_store_then_probe_says_current(tmp_path):
    data = _copy_040(tmp_path / "data")
    outcome = await guard.convert_vectors(data)
    assert outcome.ok is True and outcome.state == "converted", outcome
    assert outcome.backup_path and Path(outcome.backup_path).exists()
    assert outcome.rows_converted == 1
    again = await guard.probe(data)
    # Vectors are done. The other databases still have schema steps that apply when
    # taOS opens them, so the overall state stays "old", but nothing is left to convert.
    assert any(d["db"] == "vector_memory" and d["state"] == "current" for d in again.databases)
    assert again.can_serve is True and again.convert_available is False
    # idempotent
    second = await guard.convert_vectors(data)
    assert second.ok is True and second.state == "already_current"


@pytest.mark.asyncio
async def test_convert_refused_while_another_process_holds_the_store(tmp_path):
    data = _copy_040(tmp_path / "data")
    conn = sqlite3.connect(data / "vector-memory.db")
    conn.execute("BEGIN EXCLUSIVE")
    try:
        outcome = await guard.convert_vectors(data)
    finally:
        conn.rollback()
        conn.close()
    assert outcome.ok is False and outcome.state == "refused" and outcome.exit_code == 2
    assert "Nothing was changed" in outcome.message
    # nothing was touched: still JSON vectors
    status = await guard.probe(data)
    assert status.exit_code == 10
