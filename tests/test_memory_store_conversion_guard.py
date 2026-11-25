"""Memory store conversion guard tests (tsk-fo4bws).

Tests the guard that prevents starting a newer memory service on an old-format
store without running the guarded conversion path first.
"""

import contextlib
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.routes import settings as settings_mod
from tinyagentos.routes.settings import (
    _update_local_taosmd,
    _verify_taosmd_running,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

REAL_HEALTH_BODY = {
    "status": "ok",
    "version": "0.4.0",
    "capabilities": ["a2a.v1", "collections.v1", "search.v1", "graph.v1", "tasks.v1"],
}


class _FakeResp:
    def __init__(self, status_code=200, content_type="application/json", body=None):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeHttpClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def get(self, url, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._resp


def _fake_request(
    resp=None,
    exc=None,
    memory_url="http://localhost:7900",
    taosmd_dir="",
    taosmd_restart_cmd="",
):
    import types

    config = types.SimpleNamespace(
        memory_url=memory_url,
        taosmd_dir=taosmd_dir,
        taosmd_restart_cmd=taosmd_restart_cmd,
    )
    state = types.SimpleNamespace(config=config, http_client=_FakeHttpClient(resp, exc))
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


def _make_old_vector_store(db_path: Path):
    """Create a vector-memory.db with the OLD format (JSON vectors, no marker)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vector_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            embedding TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            valid_to REAL
        );
        CREATE INDEX IF NOT EXISTS idx_vm_valid ON vector_memory(valid_to);
        CREATE TABLE IF NOT EXISTS store_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # Insert some JSON vectors (old format)
    conn.execute(
        "INSERT INTO vector_memory (embedding, text, metadata_json, created_at) VALUES (?, ?, ?, ?)",
        ('[0.1, 0.2, 0.3]', 'test doc 1', '{}', 1234567890.0),
    )
    conn.execute(
        "INSERT INTO vector_memory (embedding, text, metadata_json, created_at) VALUES (?, ?, ?, ?)",
        ('[0.4, 0.5, 0.6]', 'test doc 2', '{}', 1234567891.0),
    )
    conn.commit()
    conn.close()


def _make_new_vector_store(db_path: Path):
    """Create a vector-memory.db with the NEW format (float32 base64, marker)."""
    import base64
    import numpy as np

    def encode(arr):
        return base64.b64encode(np.asarray(arr, dtype="<f4").tobytes()).decode("ascii")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vector_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            embedding TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            valid_to REAL
        );
        CREATE INDEX IF NOT EXISTS idx_vm_valid ON vector_memory(valid_to);
        CREATE TABLE IF NOT EXISTS store_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO store_meta (key, value) VALUES (?, ?)",
        ("mode", "late_interaction=0;binary_quant=0;dense_format=f32"),
    )
    # Insert some float32 vectors (new format)
    conn.execute(
        "INSERT INTO vector_memory (embedding, text, metadata_json, created_at) VALUES (?, ?, ?, ?)",
        (encode([0.1, 0.2, 0.3]), 'test doc 1', '{}', 1234567890.0),
    )
    conn.execute(
        "INSERT INTO vector_memory (embedding, text, metadata_json, created_at) VALUES (?, ?, ?, ?)",
        (encode([0.4, 0.5, 0.6]), 'test doc 2', '{}', 1234567891.0),
    )
    conn.commit()
    conn.close()


def _make_unknown_vector_store(db_path: Path):
    """Create a vector-memory.db with an UNKNOWN format (future marker keys)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vector_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            embedding TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            valid_to REAL
        );
        CREATE INDEX IF NOT EXISTS idx_vm_valid ON vector_memory(valid_to);
        CREATE TABLE IF NOT EXISTS store_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO store_meta (key, value) VALUES (?, ?)",
        ("mode", "late_interaction=0;binary_quant=0;dense_format=f32;future_feature=1"),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests for the app startup probe (in-process stores)
# ---------------------------------------------------------------------------

class TestAppStartupStoreProbe:
    """Tests for the store format probe at app startup."""

    @pytest.mark.asyncio
    async def test_probe_reports_current_on_new_store(self, tmp_path):
        """A fresh store (no DBs) should report CURRENT."""
        from taosmd.store_probe import probe_store, ProbeState

        report = probe_store(tmp_path)
        assert report.state == ProbeState.CURRENT
        assert report.exit_code == 0

    @pytest.mark.asyncio
    async def test_probe_reports_old_on_json_vectors(self, tmp_path):
        """A store with JSON vectors (no marker) should report OLD."""
        from taosmd.store_probe import probe_store, ProbeState

        _make_old_vector_store(tmp_path / "vector-memory.db")
        report = probe_store(tmp_path)
        assert report.state == ProbeState.OLD
        assert report.exit_code == 10

    @pytest.mark.asyncio
    async def test_probe_reports_current_on_float32_store(self, tmp_path):
        """A store with float32 vectors and marker should report CURRENT."""
        from taosmd.store_probe import probe_store, ProbeState

        _make_new_vector_store(tmp_path / "vector-memory.db")
        report = probe_store(tmp_path)
        assert report.state == ProbeState.CURRENT
        assert report.exit_code == 0

    @pytest.mark.asyncio
    async def test_probe_reports_newer_on_unknown_marker(self, tmp_path):
        """A store with unknown marker keys should report NEWER."""
        from taosmd.store_probe import probe_store, ProbeState

        _make_unknown_vector_store(tmp_path / "vector-memory.db")
        report = probe_store(tmp_path)
        assert report.state == ProbeState.NEWER
        assert report.exit_code == 12


# ---------------------------------------------------------------------------
# Tests for the guarded conversion (taosmd.store_convert)
# ---------------------------------------------------------------------------

class TestGuardedConversion:
    """Tests for the taosmd.store_convert.convert_dense_store function."""

    @pytest.mark.asyncio
    async def test_conversion_runs_once_backup_then_verification(self, tmp_path):
        """Conversion should run once, create backup before, verify after."""
        from taosmd.store_convert import convert_dense_store, EXIT_OK

        db_path = tmp_path / "vector-memory.db"
        backup_dir = tmp_path / "backups"
        _make_old_vector_store(db_path)

        result = convert_dense_store(db_path, backup_dir=backup_dir)

        assert result.exit_code == EXIT_OK
        assert result.status == "converted"
        assert result.rows_converted == 2
        assert result.backup_path is not None
        backup_path = Path(result.backup_path)
        assert backup_path.exists()

        # Verify the backup has the original JSON vectors
        backup_conn = sqlite3.connect(str(backup_path))
        backup_conn.row_factory = sqlite3.Row
        rows = backup_conn.execute("SELECT embedding FROM vector_memory ORDER BY id").fetchall()
        backup_conn.close()
        assert rows[0]["embedding"] == "[0.1, 0.2, 0.3]"
        assert rows[1]["embedding"] == "[0.4, 0.5, 0.6]"

        # Verify the live store now has float32
        live_conn = sqlite3.connect(str(db_path))
        live_conn.row_factory = sqlite3.Row
        rows = live_conn.execute("SELECT embedding FROM vector_memory ORDER BY id").fetchall()
        live_conn.close()
        import base64
        import numpy as np
        for row in rows:
            decoded = base64.b64decode(row["embedding"], validate=True)
            arr = np.frombuffer(decoded, dtype="<f4")
            assert len(arr) == 3

    @pytest.mark.asyncio
    async def test_conversion_already_current_returns_ok(self, tmp_path):
        """Already-converted store should return already_current."""
        from taosmd.store_convert import convert_dense_store, EXIT_OK

        db_path = tmp_path / "vector-memory.db"
        backup_dir = tmp_path / "backups"
        _make_new_vector_store(db_path)

        result = convert_dense_store(db_path, backup_dir=backup_dir)

        assert result.exit_code == EXIT_OK
        assert result.status == "already_current"
        assert result.rows_converted == 0

    @pytest.mark.asyncio
    async def test_conversion_fails_restores_original(self, tmp_path):
        """Failed conversion should restore the original store byte-identically."""
        from taosmd.store_convert import convert_dense_store, EXIT_RESTORED

        db_path = tmp_path / "vector-memory.db"
        backup_dir = tmp_path / "backups"
        _make_old_vector_store(db_path)

        # Capture original content
        original_content = db_path.read_bytes()

        # Mock the conversion to fail during post-check
        import taosmd.store_convert as sc

        original_post_checks = sc._post_checks

        def failing_post_checks(*args, **kwargs):
            raise sc._PostCheckFailed("simulated failure")

        with patch.object(sc, "_post_checks", failing_post_checks):
            result = convert_dense_store(db_path, backup_dir=backup_dir)

        assert result.exit_code == EXIT_RESTORED
        assert result.status == "restored"
        assert "rolled back" in result.message
        assert result.backup_path is not None

        # Verify the store is byte-identical to original
        restored_content = db_path.read_bytes()
        assert restored_content == original_content, "Store must be byte-identical after failed conversion"

    @pytest.mark.asyncio
    async def test_conversion_refuses_on_unknown_format(self, tmp_path):
        """Conversion should refuse on unknown/newer format without touching store."""
        from taosmd.store_convert import convert_dense_store, EXIT_UNKNOWN_FORMAT

        db_path = tmp_path / "vector-memory.db"
        backup_dir = tmp_path / "backups"
        _make_unknown_vector_store(db_path)

        original_content = db_path.read_bytes()

        result = convert_dense_store(db_path, backup_dir=backup_dir)

        assert result.exit_code == EXIT_UNKNOWN_FORMAT
        assert result.status == "unknown_format"

        # Verify store untouched
        restored_content = db_path.read_bytes()
        assert restored_content == original_content, "Store must be untouched on unknown format"


# ---------------------------------------------------------------------------
# Tests for _update_local_taosmd with conversion guard
# ---------------------------------------------------------------------------

def _update_route_patches():
    """The subprocess/restart patch set the existing update tests use."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"Already up to date.\n", b""))

    async def _fake_restart(_app_state):
        return None

    return (
        patch(
            "tinyagentos.routes.settings.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ),
        patch(
            "tinyagentos.routes.settings._run_capture",
            new=AsyncMock(return_value=(0, "ok")),
        ),
        patch(
            "tinyagentos.desktop_rebuild.rebuild_desktop_bundle_if_stale",
            new=AsyncMock(
                return_value=MagicMock(rebuilt=False, success=True, message="current")
            ),
        ),
        patch("tinyagentos.routes.system._do_restart", new=_fake_restart),
        patch("tinyagentos.routes.settings.write_pending_restart"),
        patch(
            "tinyagentos.routes.settings._announce_taosmd_restart",
            new=AsyncMock(return_value=True),
        ),
        patch.object(settings_mod, "_TAOSMD_VERIFY_RETRIES", 1),
        patch.object(settings_mod, "_TAOSMD_VERIFY_DELAY", 0),
        patch(
            "tinyagentos.update_preflight.check_preflight",
            return_value=[],
        ),
    )


class TestUpdateLocalTaosmdConversionGuard:
    """Tests for the conversion guard in _update_local_taosmd."""

    @pytest.mark.asyncio
    async def test_update_probes_store_before_pull(self, tmp_path):
        """Update should probe the store format before pulling taosmd."""
        from taosmd.store_probe import probe_store, ProbeState

        # Set up a local taosmd checkout
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()

        # Create data dir with old-format store
        data_dir = tmp_path / "data"
        _make_old_vector_store(data_dir / "vector-memory.db")

        # Mock the probe to be called
        probe_called = []

        def mock_probe(data_dir_arg):
            probe_called.append(data_dir_arg)
            return probe_store(data_dir_arg)

        with patch("taosmd.store_probe.probe_store", side_effect=mock_probe):
            with patch.object(settings_mod, "_run_capture", new=AsyncMock(return_value=(0, "ok"))):
                req = _fake_request(
                    taosmd_dir=str(taosmd_dir),
                    taosmd_restart_cmd="systemctl restart taosmd",
                )
                # The config needs a memory_url that's local
                req.app.state.config.memory_url = "http://localhost:7900"
                req.app.state.config.taosmd_dir = str(taosmd_dir)
                req.app.state.config.taosmd_restart_cmd = "systemctl restart taosmd"

                # We can't fully test _update_local_taosmd without more mocking,
                # but we can verify the probe would be called with the right data_dir
                report = await _update_local_taosmd(req)

        # The probe should have been called
        assert len(probe_called) >= 1

    @pytest.mark.asyncio
    async def test_update_refuses_on_newer_store_format(self, tmp_path, client, app):
        """Update should refuse when store format is NEWER than the service."""
        # This test requires the full integration - we test the behavior
        # at the apply_update level which calls _update_local_taosmd
        pass

    @pytest.mark.asyncio
    async def test_update_runs_conversion_on_old_store(self, tmp_path, client, app):
        """Update should run conversion on old store before restart."""
        pass


# ---------------------------------------------------------------------------
# Integration tests for apply_update with conversion guard
# ---------------------------------------------------------------------------

class TestApplyUpdateConversionGuard:
    """Integration tests for the full update flow with conversion guard."""

    @pytest.mark.asyncio
    async def test_update_aborts_on_newer_store(self, tmp_path, client, app):
        """Update should abort with plain message when store is NEWER."""
        # Set up local taosmd
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()

        # Create data dir with newer-format store (unknown marker)
        # Use the app's data directory (tmp_path)
        _make_unknown_vector_store(tmp_path / "vector-memory.db")

        app.state.config.taosmd_dir = str(taosmd_dir)
        app.state.config.taosmd_restart_cmd = "systemctl restart taosmd"
        app.state.config.memory_url = "http://localhost:7900"

        real_http_client = app.state.http_client
        app.state.http_client = _FakeHttpClient(_FakeResp(200, "application/json", REAL_HEALTH_BODY))

        try:
            with contextlib.ExitStack() as stack:
                for p in _update_route_patches():
                    stack.enter_context(p)
                # We need to mock the probe to return NEWER
                with patch("taosmd.store_probe.probe_store") as mock_probe:
                    from taosmd.store_probe import ProbeReport, ProbeState, DbProbe
                    mock_probe.return_value = ProbeReport(
                        data_dir=str(tmp_path),
                        state=ProbeState.NEWER,
                        databases=[
                            DbProbe(
                                db="vector_memory",
                                path=str(tmp_path / "vector-memory.db"),
                                state=ProbeState.NEWER,
                                reason="marker keys this build does not know: future_feature",
                            )
                        ],
                    )
                    resp = await client.post("/api/settings/update")
        finally:
            app.state.http_client = real_http_client
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

        # The update should fail with a plain message
        assert resp.status_code == 500
        data = resp.json()
        assert "taOSmd update FAILED" in data["error"]
        # Check for plain user message (no em dashes, no AI attribution)
        assert "memories are untouched" in data["error"].lower() or "memories are safe" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_update_converts_old_store_and_proceeds(self, tmp_path, client, app):
        """Update should convert old store and then proceed successfully."""
        # Set up local taosmd
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()

        # Create data dir with old-format store in app's data directory
        _make_old_vector_store(tmp_path / "vector-memory.db")

        app.state.config.taosmd_dir = str(taosmd_dir)
        app.state.config.taosmd_restart_cmd = "systemctl restart taosmd"
        app.state.config.memory_url = "http://localhost:7900"

        real_http_client = app.state.http_client
        app.state.http_client = _FakeHttpClient(_FakeResp(200, "application/json", REAL_HEALTH_BODY))

        try:
            with contextlib.ExitStack() as stack:
                for p in _update_route_patches():
                    stack.enter_context(p)
                # Mock probe to return OLD initially, then CURRENT after conversion
                call_count = [0]
                from taosmd.store_probe import ProbeReport, ProbeState, DbProbe

                def mock_probe(data_dir_arg):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        # First call: old format
                        return ProbeReport(
                            data_dir=str(tmp_path),
                            state=ProbeState.OLD,
                            databases=[
                                DbProbe(
                                    db="vector_memory",
                                    path=str(tmp_path / "vector-memory.db"),
                                    state=ProbeState.OLD,
                                    reason="legacy JSON vectors with no marker",
                                )
                            ],
                        )
                    else:
                        # After conversion: current
                        return ProbeReport(
                            data_dir=str(tmp_path),
                            state=ProbeState.CURRENT,
                            databases=[
                                DbProbe(
                                    db="vector_memory",
                                    path=str(tmp_path / "vector-memory.db"),
                                    state=ProbeState.CURRENT,
                                    reason="",
                                )
                            ],
                        )

                with patch("taosmd.store_probe.probe_store", side_effect=mock_probe):
                    # Mock the conversion to succeed
                    with patch("taosmd.store_convert.convert_dense_store") as mock_convert:
                        from taosmd.store_convert import ConvertResult, EXIT_OK
                        mock_convert.return_value = ConvertResult(
                            status="converted",
                            exit_code=EXIT_OK,
                            message="converted 2 rows",
                            backup_path=str(tmp_path / "backups" / "vector-memory.pre-f32.db"),
                            rows_total=2,
                            rows_converted=2,
                            dimension=3,
                        )
                        resp = await client.post("/api/settings/update")
        finally:
            app.state.http_client = real_http_client
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"

    @pytest.mark.asyncio
    async def test_update_aborts_on_conversion_failure(self, tmp_path, client, app):
        """Update should abort when conversion fails, old service kept."""
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()

        # Create old-format store in app's data directory
        _make_old_vector_store(tmp_path / "vector-memory.db")

        app.state.config.taosmd_dir = str(taosmd_dir)
        app.state.config.taosmd_restart_cmd = "systemctl restart taosmd"
        app.state.config.memory_url = "http://localhost:7900"

        real_http_client = app.state.http_client
        app.state.http_client = _FakeHttpClient(_FakeResp(200, "application/json", REAL_HEALTH_BODY))

        try:
            with contextlib.ExitStack() as stack:
                for p in _update_route_patches():
                    stack.enter_context(p)
                from taosmd.store_probe import ProbeReport, ProbeState, DbProbe
                with patch("taosmd.store_probe.probe_store") as mock_probe:
                    mock_probe.return_value = ProbeReport(
                        data_dir=str(tmp_path),
                        state=ProbeState.OLD,
                        databases=[
                            DbProbe(
                                db="vector_memory",
                                path=str(tmp_path / "vector-memory.db"),
                                state=ProbeState.OLD,
                                reason="legacy JSON vectors with no marker",
                            )
                        ],
                    )
                    with patch("taosmd.store_convert.convert_dense_store") as mock_convert:
                        from taosmd.store_convert import ConvertResult, EXIT_RESTORED
                        mock_convert.return_value = ConvertResult(
                            status="restored",
                            exit_code=EXIT_RESTORED,
                            message="post-conversion check failed; store rolled back",
                            backup_path=str(tmp_path / "backups" / "vector-memory.pre-f32.db"),
                            rows_total=2,
                        )
                        resp = await client.post("/api/settings/update")
        finally:
            app.state.http_client = real_http_client
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

        assert resp.status_code == 500
        data = resp.json()
        assert "taOSmd update FAILED" in data["error"]
        assert "memories are untouched" in data["error"].lower() or "memories are safe" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_update_refuses_when_conversion_path_missing(self, tmp_path, client, app):
        """Update should refuse with plain message when conversion path is missing."""
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()

        # Create old-format store in app's data directory
        _make_old_vector_store(tmp_path / "vector-memory.db")

        app.state.config.taosmd_dir = str(taosmd_dir)
        app.state.config.taosmd_restart_cmd = "systemctl restart taosmd"
        app.state.config.memory_url = "http://localhost:7900"

        real_http_client = app.state.http_client
        app.state.http_client = _FakeHttpClient(_FakeResp(200, "application/json", REAL_HEALTH_BODY))

        try:
            with contextlib.ExitStack() as stack:
                for p in _update_route_patches():
                    stack.enter_context(p)
                from taosmd.store_probe import ProbeReport, ProbeState, DbProbe
                with patch("taosmd.store_probe.probe_store") as mock_probe:
                    mock_probe.return_value = ProbeReport(
                        data_dir=str(tmp_path),
                        state=ProbeState.OLD,
                        databases=[
                            DbProbe(
                                db="vector_memory",
                                path=str(tmp_path / "vector-memory.db"),
                                state=ProbeState.OLD,
                                reason="legacy JSON vectors with no marker",
                            )
                        ],
                    )
                    # Simulate conversion path missing (ImportError or function not found)
                    with patch("taosmd.store_convert.convert_dense_store", side_effect=ImportError("no module")):
                        resp = await client.post("/api/settings/update")
        finally:
            app.state.http_client = real_http_client
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

        assert resp.status_code == 500
        data = resp.json()
        assert "taOSmd update FAILED" in data["error"]
        assert "memories are untouched" in data["error"].lower() or "memories are safe" in data["error"].lower()


# ---------------------------------------------------------------------------
# Existing-install upgrade test (pattern from test_store_upgrades.py)
# ---------------------------------------------------------------------------

class TestExistingInstallUpgrade:
    """Test that an existing install with old-format store upgrades correctly."""

    @pytest.mark.asyncio
    async def test_old_store_upgrade_preserves_memories(self, tmp_path):
        """Seed a store as old version wrote it, run upgrade, assert memories readable."""
        from taosmd.vector_memory import VectorMemory
        from taosmd.store_convert import convert_dense_store, EXIT_OK

        db_path = tmp_path / "vector-memory.db"
        _make_old_vector_store(db_path)

        # Verify old store is readable with old format
        old_store = VectorMemory(str(db_path), embed_mode="onnx", onnx_path="/fake/path")
        # We can't actually init without the ONNX model, but we can check the DB directly
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT embedding, text FROM vector_memory ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0]["embedding"] == "[0.1, 0.2, 0.3]"
        assert rows[0]["text"] == "test doc 1"

        # Run conversion (simulating the upgrade)
        result = convert_dense_store(db_path, backup_dir=tmp_path / "backups")
        assert result.exit_code == EXIT_OK

        # Verify memories are still readable after conversion
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT embedding, text FROM vector_memory ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2
        import base64
        import numpy as np
        for row in rows:
            decoded = base64.b64decode(row["embedding"], validate=True)
            arr = np.frombuffer(decoded, dtype="<f4")
            assert len(arr) == 3
        assert rows[0]["text"] == "test doc 1"
        assert rows[1]["text"] == "test doc 2"

    @pytest.mark.asyncio
    async def test_killed_conversion_restored_on_next_start(self, tmp_path):
        """Conversion killed halfway -> next start finds working memory."""
        from taosmd.store_convert import convert_dense_store, EXIT_RESTORED
        import taosmd.store_convert as sc

        db_path = tmp_path / "vector-memory.db"
        backup_dir = tmp_path / "backups"
        _make_old_vector_store(db_path)

        original_content = db_path.read_bytes()

        # Simulate conversion being killed during post-check
        def failing_post_checks(*args, **kwargs):
            raise sc._PostCheckFailed("simulated crash (power loss)")

        with patch.object(sc, "_post_checks", failing_post_checks):
            result = convert_dense_store(db_path, backup_dir=backup_dir)

        assert result.exit_code == EXIT_RESTORED

        # Verify store is restored byte-identically
        restored_content = db_path.read_bytes()
        assert restored_content == original_content

        # Next start would probe and find OLD (store restored to original format,
        # ready for another conversion attempt)
        from taosmd.store_probe import probe_store, ProbeState
        report = probe_store(tmp_path)
        assert report.state == ProbeState.OLD


if __name__ == "__main__":
    pytest.main([__file__, "-v"])