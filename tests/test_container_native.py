"""Tests for the bare-metal NativeBackend."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.containers.native import (
    NativeBackend,
    _ALLOWED_WRITE_ROOTS,
    _has_systemd,
    _safe_host_path,
    _service_unit_path,
    _SYSTEMD_DIR,
)


class TestSafeHostPath:
    """Tests for the path-traversal guard."""

    def test_rejects_relative_path(self):
        assert _safe_host_path("foo/bar") is None
        assert _safe_host_path("../etc/passwd") is None

    def test_rejects_dotdot_traversal(self):
        assert _safe_host_path("/opt/taos/../../etc/passwd") is None
        assert _safe_host_path("/tmp/../etc/shadow") is None

    def test_rejects_outside_allowed_roots(self):
        assert _safe_host_path("/etc/passwd") is None
        assert _safe_host_path("/home/user/.bashrc") is None
        assert _safe_host_path("/var/log/syslog") is None

    def test_accepts_allowed_roots(self):
        assert _safe_host_path("/opt/taos/config.yaml") == "/opt/taos/config.yaml"
        assert _safe_host_path("/tmp/test.txt") is not None
        assert _safe_host_path("/etc/systemd/system/foo.service") is not None
        assert _safe_host_path("/var/lib/taos/agents/foo/data") is not None

    def test_accepts_resolved_symlink_within_root(self, tmp_path, monkeypatch):
        # Create a symlink inside an allowed root that points within the same root
        allowed = tmp_path / "opt" / "taos"
        allowed.mkdir(parents=True)
        (allowed / "real.txt").write_text("hello")
        link = allowed / "link.txt"
        os.symlink(str(allowed / "real.txt"), str(link))
        result = _safe_host_path(str(link), allowed_roots=(str(allowed) + "/",))
        assert result is not None

    def test_rejects_symlink_outside_root(self, tmp_path):
        # Symlink inside allowed root pointing outside
        allowed = tmp_path / "opt" / "taos"
        allowed.mkdir(parents=True)
        outside = tmp_path / "secret.txt"
        outside.write_text("secret")
        link = allowed / "escape.txt"
        os.symlink(str(outside), str(link))
        result = _safe_host_path(str(link), allowed_roots=(str(allowed) + "/",))
        assert result is None

    def test_rejects_empty_path(self):
        assert _safe_host_path("") is None

    def test_rejects_non_absolute_path(self):
        assert _safe_host_path("relative/file.txt") is None


class TestNativeBackendE2E:
    """End-to-end tests against the real filesystem (no mocks).

    These tests write unit files into a temporary directory and clean up
    afterwards.  They do NOT require systemd — they test the backend's
    logic directly when systemd is unavailable (the fallback path).
    """

    @pytest.fixture
    def backend(self, tmp_path, monkeypatch):
        """Return a NativeBackend with a temp _SYSTEMD_DIR and systemd mocked away."""
        import tinyagentos.containers.native as _mod
        monkeypatch.setattr(_mod, "_SYSTEMD_DIR", tmp_path)
        # Force systemd-unavailable path so tests don't need root
        monkeypatch.setattr(_mod, "_has_systemd", lambda: False)
        monkeypatch.setattr(_mod, "_systemd_dir_is_writable", lambda: False)
        return NativeBackend()

    # --- create_container ---

    @pytest.mark.asyncio
    async def test_create_container_no_systemd_returns_false(self, backend):
        """When systemd is unavailable, create_container must return success:False."""
        result = await backend.create_container("taos-agent-foo")
        assert result["success"] is False
        assert "systemd not available" in result.get("error", "")

    # --- list_containers (fallback path) ---

    @pytest.mark.asyncio
    async def test_list_containers_fallback_empty(self, backend, tmp_path):
        result = await backend.list_containers("taos-agent-")
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_list_containers_fallback_finds_units(self, backend, tmp_path, monkeypatch):
        import tinyagentos.containers.native as _mod
        # Create two service files
        (tmp_path / "taos-agent-bar.service").write_text("[Unit]\n")
        (tmp_path / "taos-agent-baz.service").write_text("[Unit]\n")
        (tmp_path / "other.service").write_text("[Unit]\n")  # should not match
        monkeypatch.setattr(_mod, "_has_systemd", lambda: False)

        result = await backend.list_containers("taos-agent-")
        names = {c.name for c in result}
        assert names == {"taos-agent-bar", "taos-agent-baz"}

    # --- exec_in_container ---

    @pytest.mark.asyncio
    async def test_exec_in_container_runs_command(self, backend):
        code, output = await backend.exec_in_container(
            "ignored", ["echo", "hello", "world"], timeout=10,
        )
        assert code == 0
        assert "hello world" in output

    @pytest.mark.asyncio
    async def test_exec_in_container_returns_nonzero(self, backend):
        code, output = await backend.exec_in_container(
            "ignored", ["bash", "-c", "exit 42"], timeout=10,
        )
        assert code == 42

    # --- push_file ---

    @pytest.mark.asyncio
    async def test_push_file_copies_content(self, backend, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("hello bare metal")
        dest = tmp_path / "dest.txt"
        code, output = await backend.push_file("ignored", str(src), str(dest))
        assert code == 0
        assert dest.read_text() == "hello bare metal"

    @pytest.mark.asyncio
    async def test_push_file_creates_dirs(self, backend, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("data")
        dest = tmp_path / "sub" / "deep" / "target.txt"
        code, output = await backend.push_file("ignored", str(src), str(dest))
        assert code == 0
        assert dest.read_text() == "data"

    @pytest.mark.asyncio
    async def test_push_file_rejects_path_traversal(self, backend, tmp_path):
        """push_file must reject remote_path containing .. segments."""
        src = tmp_path / "source.txt"
        src.write_text("evil")
        code, output = await backend.push_file(
            "ignored", str(src), "/opt/taos/../../etc/cron.d/evil",
        )
        assert code != 0
        assert "unsafe path" in output.lower()

    @pytest.mark.asyncio
    async def test_push_file_rejects_outside_allowed_roots(self, backend, tmp_path):
        """push_file must reject paths outside _ALLOWED_WRITE_ROOTS."""
        src = tmp_path / "source.txt"
        src.write_text("data")
        code, output = await backend.push_file(
            "ignored", str(src), "/home/user/.bashrc",
        )
        assert code != 0
        assert "unsafe path" in output.lower()

    @pytest.mark.asyncio
    async def test_push_file_accepts_allowed_root(self, backend, tmp_path, monkeypatch):
        """push_file must accept paths within _ALLOWED_WRITE_ROOTS."""
        import tinyagentos.containers.native as _mod
        # Override allowed roots to include our tmp_path
        monkeypatch.setattr(
            _mod, "_ALLOWED_WRITE_ROOTS", (str(tmp_path) + "/",),
        )
        src = tmp_path / "source.txt"
        src.write_text("safe data")
        dest = tmp_path / "agent" / "config.yaml"
        code, output = await backend.push_file("ignored", str(src), str(dest))
        assert code == 0
        assert dest.read_text() == "safe data"

    # --- start / stop / destroy ---

    @pytest.mark.asyncio
    async def test_start_container_no_systemd(self, backend):
        """When systemd is unavailable, start_container must return success:False."""
        result = await backend.start_container("foo")
        assert result["success"] is False
        assert "systemd not available" in result.get("output", "")

    @pytest.mark.asyncio
    async def test_stop_container_no_systemd(self, backend):
        result = await backend.stop_container("foo")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_destroy_container_no_systemd(self, backend):
        result = await backend.destroy_container("foo")
        assert result["success"] is True

    # --- add_proxy_device ---

    @pytest.mark.asyncio
    async def test_add_proxy_device_noop(self, backend):
        result = await backend.add_proxy_device(
            "foo", "dev1", "tcp:127.0.0.1:4000", "tcp:127.0.0.1:4000",
        )
        assert result["success"] is True
        assert "not needed" in result.get("output", "")

    # --- snapshots ---

    @pytest.mark.asyncio
    async def test_snapshot_create_returns_false(self, backend):
        result = await backend.snapshot_create("foo", "snap1")
        assert result["success"] is False
        assert "not supported" in result.get("note", "")

    @pytest.mark.asyncio
    async def test_snapshot_restore_returns_false(self, backend):
        result = await backend.snapshot_restore("foo", "snap1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_snapshot_list_returns_false(self, backend):
        result = await backend.snapshot_list("foo")
        assert result["success"] is False

    # --- set_root_quota ---

    @pytest.mark.asyncio
    async def test_set_root_quota_returns_success_with_note(self, backend):
        result = await backend.set_root_quota("foo", 10)
        assert result["success"] is True
        assert "not enforced" in result.get("note", "")

    # --- set_env ---

    @pytest.mark.asyncio
    async def test_set_env_no_systemd(self, backend):
        result = await backend.set_env("foo", "KEY", "VALUE")
        assert result["success"] is True
        assert "systemd not available" in result.get("output", "")


class TestNativeBackendWithSystemd:
    """Tests that mock systemd CLI calls."""

    @pytest.fixture
    def backend(self):
        return NativeBackend()

    def _mock_run(self, monkeypatch, return_values):
        """Patch _run in the native module to return canned responses."""
        async def _fake_run(cmd, timeout=120):
            key = " ".join(str(c) for c in cmd)
            return return_values.get(key, (0, ""))
        monkeypatch.setattr(
            "tinyagentos.containers.native._run", _fake_run,
        )

    def _setup_systemd_mocks(self, monkeypatch, tmp_path, *, unit_dir_writable=True):
        """Common setup for systemd-present tests."""
        monkeypatch.setattr(
            "tinyagentos.containers.native._SYSTEMD_DIR", tmp_path,
        )
        monkeypatch.setattr(
            "tinyagentos.containers.native._has_systemd", lambda: True,
        )
        monkeypatch.setattr(
            "tinyagentos.containers.native._systemd_dir_is_writable",
            lambda: unit_dir_writable,
        )

    @pytest.mark.asyncio
    async def test_create_container_stub_unit(self, backend, tmp_path, monkeypatch):
        """create_container must produce a stub with ExecStart=/bin/false."""
        self._setup_systemd_mocks(monkeypatch, tmp_path)
        self._mock_run(monkeypatch, {"systemctl daemon-reload": (0, "")})

        result = await backend.create_container(
            "taos-agent-test",
            memory_limit="512MB",
            cpu_limit=2,
            env={"FOO": "bar", "BAZ": "qux"},
        )
        assert result["success"] is True
        assert result["name"] == "taos-agent-test"
        assert "stub unit created" in result.get("note", "")

        unit_path = tmp_path / "taos-agent-test.service"
        assert unit_path.exists()
        content = unit_path.read_text()
        assert "Description=taOS Agent: taos-agent-test" in content
        assert "ExecStart=/bin/false" in content
        assert "Restart=no" in content
        assert "MemoryLimit=512MB" in content
        assert "CPUQuota=200%" in content
        assert "Environment=FOO=bar" in content
        assert "Environment=BAZ=qux" in content

    @pytest.mark.asyncio
    async def test_create_container_readonly_unit_dir(self, backend, tmp_path, monkeypatch):
        """When _SYSTEMD_DIR is not writable, create_container must fail."""
        self._setup_systemd_mocks(monkeypatch, tmp_path, unit_dir_writable=False)
        self._mock_run(monkeypatch, {})

        result = await backend.create_container("taos-agent-test")
        assert result["success"] is False
        assert "not writable" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_list_containers_parses_active_column(self, backend, monkeypatch):
        """list_containers must parse ACTIVE (parts[2]), not SUB (parts[3])."""
        monkeypatch.setattr(
            "tinyagentos.containers.native._has_systemd", lambda: True,
        )
        # systemctl output: UNIT LOAD ACTIVE SUB DESCRIPTION
        # "active running" means ACTIVE=active, SUB=running
        # The old code read parts[3]=running (SUB) and mapped it via
        # .capitalize() -> "Running", which was accidentally correct
        # for running services. But for inactive/dead, it would read
        # "dead" (SUB) instead of "inactive" (ACTIVE) and miss the
        # Stopped mapping. This test verifies the fix reads parts[2].
        self._mock_run(monkeypatch, {
            "systemctl list-units --type=service --all --no-legend --no-pager taos-agent-*": (
                0,
                "taos-agent-foo.service  loaded  active  running  Foo agent\n"
                "taos-agent-bar.service  loaded  inactive  dead  Bar agent (stopped)\n"
                "taos-agent-baz.service  loaded  failed  failed  Baz agent (crashed)\n"
            ),
        })
        result = await backend.list_containers("taos-agent-")
        assert len(result) == 3
        statuses = {c.name: c.status for c in result}
        # active → Running
        assert statuses["taos-agent-foo"] == "Running"
        # inactive → Stopped
        assert statuses["taos-agent-bar"] == "Stopped"
        # failed → Stopped
        assert statuses["taos-agent-baz"] == "Stopped"

    @pytest.mark.asyncio
    async def test_destroy_container_stops_and_removes(self, backend, tmp_path, monkeypatch):
        self._setup_systemd_mocks(monkeypatch, tmp_path)
        self._mock_run(monkeypatch, {
            "systemctl stop taos-agent-foo.service": (0, ""),
            "systemctl disable taos-agent-foo.service": (0, ""),
            "systemctl daemon-reload": (0, ""),
        })
        # Create the unit file first
        (tmp_path / "taos-agent-foo.service").write_text("[Unit]\n")

        result = await backend.destroy_container("taos-agent-foo")
        assert result["success"] is True
        assert not (tmp_path / "taos-agent-foo.service").exists()

    @pytest.mark.asyncio
    async def test_rename_container(self, backend, tmp_path, monkeypatch):
        self._setup_systemd_mocks(monkeypatch, tmp_path)
        self._mock_run(monkeypatch, {"systemctl daemon-reload": (0, "")})
        (tmp_path / "taos-agent-old.service").write_text("[Unit]\n")

        result = await backend.rename_container("taos-agent-old", "taos-agent-new")
        assert result["success"] is True
        assert not (tmp_path / "taos-agent-old.service").exists()
        assert (tmp_path / "taos-agent-new.service").exists()

    @pytest.mark.asyncio
    async def test_set_env_updates_unit_file(self, backend, tmp_path, monkeypatch):
        self._setup_systemd_mocks(monkeypatch, tmp_path)
        self._mock_run(monkeypatch, {"systemctl daemon-reload": (0, "")})

        unit_path = tmp_path / "taos-agent-env.service"
        unit_path.write_text("""[Unit]
Description=Test

[Service]
ExecStart=/bin/false
Environment=OLD_KEY=old_value

[Install]
WantedBy=multi-user.target
""")

        result = await backend.set_env("taos-agent-env", "NEW_KEY", "new_value")
        assert result["success"] is True

        content = unit_path.read_text()
        assert "Environment=NEW_KEY=new_value" in content
        assert "Environment=OLD_KEY=old_value" in content  # preserved
        assert "ExecStart=/bin/false" in content  # unchanged

    @pytest.mark.asyncio
    async def test_set_env_replaces_existing_key(self, backend, tmp_path, monkeypatch):
        self._setup_systemd_mocks(monkeypatch, tmp_path)
        self._mock_run(monkeypatch, {"systemctl daemon-reload": (0, "")})

        unit_path = tmp_path / "taos-agent-env2.service"
        unit_path.write_text("""[Unit]
[Service]
ExecStart=/bin/false
Environment=KEY=old_value
""")

        result = await backend.set_env("taos-agent-env2", "KEY", "new_value")
        assert result["success"] is True

        content = unit_path.read_text()
        assert "Environment=KEY=new_value" in content
        assert "Environment=KEY=old_value" not in content

    @pytest.mark.asyncio
    async def test_set_env_execstart_wires_payload(self, backend, tmp_path, monkeypatch):
        """set_env with key='ExecStart' must replace the ExecStart= line."""
        self._setup_systemd_mocks(monkeypatch, tmp_path)
        self._mock_run(monkeypatch, {"systemctl daemon-reload": (0, "")})

        unit_path = tmp_path / "taos-agent-exec.service"
        unit_path.write_text("""[Unit]
[Service]
ExecStart=/bin/false
Environment=FOO=bar

[Install]
WantedBy=multi-user.target
""")

        result = await backend.set_env(
            "taos-agent-exec", "ExecStart", "/usr/local/bin/taos-agent --serve",
        )
        assert result["success"] is True

        content = unit_path.read_text()
        assert "ExecStart=/usr/local/bin/taos-agent --serve" in content
        assert "ExecStart=/bin/false" not in content
        assert "Environment=FOO=bar" in content  # preserved

    @pytest.mark.asyncio
    async def test_get_container_logs(self, backend, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.containers.native._has_systemd", lambda: True,
        )
        self._mock_run(monkeypatch, {
            "journalctl --no-pager -n 42 -u foo.service": (0, "log line 1\nlog line 2\n"),
        })
        logs = await backend.get_container_logs("foo", lines=42)
        assert "log line 1" in logs


class TestHelperFunctions:
    def test_service_unit_path(self):
        path = _service_unit_path("taos-agent-foo")
        assert path.name == "taos-agent-foo.service"
        assert str(_SYSTEMD_DIR) in str(path)

    def test_has_systemd(self):
        result = _has_systemd()
        # On the test host this may be True or False — just verify it's a bool
        assert isinstance(result, bool)
