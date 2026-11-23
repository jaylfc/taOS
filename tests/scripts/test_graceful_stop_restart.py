"""Tests that a controller restart never drains the agents while a real
reboot/shutdown/halt still does exactly once (task tsk-kzbxia).

The drain now only runs for a real system shutdown: systemd queues a
shutdown.target/reboot.target/halt.target job while one is in progress, and
scripts/taos-graceful-stop.sh skips the ``/api/system/prepare-shutdown`` call
when no such job is queued. A controller restart (``systemctl restart``, the
in-app Restart button, or an update restart) has no shutdown job, so it must
not drain. The pre-shutdown hook (systemd/taos-pre-shutdown.service) remains
the single drain entry point for real reboots.

These tests drive the real scripts/taos-graceful-stop.sh with a stub curl that
records every call, mirroring tests/scripts/test_graceful_stop_stamp.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "taos-graceful-stop.sh"
SYSTEM_UNIT = Path(__file__).resolve().parents[2] / "scripts" / "systemd" / "tinyagentos.service"
PRE_SHUTDOWN_UNIT = Path(__file__).resolve().parents[2] / "systemd" / "taos-pre-shutdown.service"

CURL_STUB = '#!/bin/sh\necho "$@" >> "$CURL_LOG"\nexit "${CURL_EXIT:-0}"\n'

# install-server.sh substitutes these variables when stamping the unit template.
SED_SUBS = [
    ("TAOS_USER", "taos"),
    ("TAOS_GROUP", "taos"),
    ("TAOS_INSTALL_DIR", "/srv/tinyagentos"),
    ("TAOS_PYTHON", "/srv/tinyagentos/.venv/bin/python"),
    ("TAOS_PORT", "6969"),
    ("TAOS_STOP_SCRIPT", "/usr/local/bin/taos-graceful-stop"),
    ("__TAOS_PREFETCH_VALUE__", "0"),
]


def _render_unit(template: Path) -> str:
    """Render a unit template the way install-server.sh does (sed substitutions)."""
    text = template.read_text()
    for old, new in SED_SUBS:
        text = text.replace(old, new)
    return text


class FakeHost:
    """A throwaway install layout plus a curl stub that records every call."""

    def __init__(self, tmp_path: Path, *, shutdown_job: bool) -> None:
        self.runtime = tmp_path / "run" / "taos"      # systemd RuntimeDirectory=taos
        self.runtime.mkdir(parents=True)
        self.runtime.chmod(0o750)
        self.data = tmp_path / "install" / "data"     # no-systemd nohup install
        self.data.mkdir(parents=True)
        self.data.chmod(0o700)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.curl_log = tmp_path / "curl.log"
        bindir = tmp_path / "bin"
        bindir.mkdir()
        curl = bindir / "curl"
        curl.write_text(CURL_STUB)
        curl.chmod(0o755)
        # Stub systemctl: systemd queues a shutdown/reboot/halt target job only
        # during a real shutdown. A controller restart has no such job.
        systemctl = bindir / "systemctl"
        if shutdown_job:
            systemctl.write_text("#!/bin/sh\necho \"1 reboot.target start\"\nexit 0\n")
        else:
            systemctl.write_text("#!/bin/sh\necho \"80 networking.service start running\"\nexit 0\n")
        systemctl.chmod(0o755)
        self._bindir = bindir

    def run(self, **overrides) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = f"{self._bindir}:{env['PATH']}"
        env["HOME"] = str(self.home)
        env["CURL_LOG"] = str(self.curl_log)
        env["RUNTIME_DIRECTORY"] = str(self.runtime)
        env["TAOS_INSTALL_DIR"] = str(self.data.parent)
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        return subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
        )

    @property
    def drain_calls(self) -> list[str]:
        if not self.curl_log.exists():
            return []
        return [ln for ln in self.curl_log.read_text().splitlines() if ln.strip()]


@pytest.fixture
def host(tmp_path: Path):
    return FakeHost(tmp_path, shutdown_job=False)


@pytest.fixture
def reboot_host(tmp_path: Path):
    return FakeHost(tmp_path, shutdown_job=True)


class TestGracefulStopRestartVsReboot:
    def test_restart_stop_makes_no_prepare_shutdown_request(self, host: FakeHost) -> None:
        """A controller restart must not call /api/system/prepare-shutdown."""
        result = host.run()
        assert result.returncode == 0, result.stderr
        assert host.drain_calls == [], (
            f"restart-shaped stop must not drain, got calls: {host.drain_calls}"
        )

    def test_reboot_stop_makes_exactly_one_prepare_shutdown_request(
        self, reboot_host: FakeHost
    ) -> None:
        """A real reboot drains exactly once (60 s stamp dedupe still applies)."""
        result = reboot_host.run()
        assert result.returncode == 0, result.stderr
        assert len(reboot_host.drain_calls) == 1, (
            f"reboot-shaped stop must drain exactly once, got {len(reboot_host.drain_calls)}: "
            f"{reboot_host.drain_calls}"
        )


class TestUnitTemplates:
    def test_restart_unit_has_no_drain_command(self) -> None:
        """The rendered tinyagentos.service must not drain on stop/restart."""
        rendered = _render_unit(SYSTEM_UNIT)
        assert not re.search(r"(?m)^\s*ExecStop=", rendered), (
            f"restart unit must not drain:\n{rendered}"
        )

    def test_pre_shutdown_unit_still_has_drain_command(self) -> None:
        """taos-pre-shutdown.service must still run the drain on real reboots."""
        text = PRE_SHUTDOWN_UNIT.read_text()
        assert "ExecStart=/usr/local/bin/taos-graceful-stop" in text, text
        assert "shutdown.target reboot.target halt.target" in text, text


class TestRestartPrepareAppLevel:
    @pytest.mark.asyncio
    async def test_restart_prepare_posts_no_graceful_shutdown_notice(
        self, client, monkeypatch, tmp_path
    ) -> None:
        """POST /api/system/restart/prepare must not drain: no
        "Graceful shutdown started" notification and no new resume_note.json."""
        from tinyagentos.restart_orchestrator import RestartOrchestrator

        state = client._transport.app.state
        monkeypatch.setattr(state, "orchestrator", RestartOrchestrator(state))
        monkeypatch.setattr(state, "auto_updater", None, raising=False)
        from unittest.mock import patch

        with patch("tinyagentos.routes.system._do_restart"):
            resp = await client.post("/api/system/restart/prepare")
        assert resp.status_code == 200, resp.text

        notifications = state.notifications
        notes = await notifications.list(limit=100)
        titles = {note.get("title") for note in notes}
        assert "Graceful shutdown started" not in titles, titles

        data_dir = state.data_dir
        agent_memory = data_dir / "agent-memory"
        resume_notes = (
            list(agent_memory.glob("*/resume_note.json")) if agent_memory.is_dir() else []
        )
        assert not resume_notes, resume_notes
