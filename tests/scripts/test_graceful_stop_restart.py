"""Tests that a controller restart never drains the agents while a real
reboot/shutdown/halt/poweroff/kexec still drains exactly once (task tsk-kzbxia).

The drain now only runs for a real system shutdown: systemd queues a
shutdown.target/reboot.target/halt.target/poweroff.target/kexec.target job while
one is in progress, and scripts/taos-graceful-stop.sh skips the
``/api/system/prepare-shutdown`` call when no such job is queued. A controller
restart (``systemctl restart``, the in-app Restart button, or an update restart)
has no shutdown job, so it must not drain. The pre-shutdown hook
(systemd/taos-pre-shutdown.service) remains the single drain entry point for
real reboots.

These tests drive the real scripts/taos-graceful-stop.sh with a stub curl that
records every call, mirroring tests/scripts/test_graceful_stop_stamp.py.

TestRefreshArtifacts exercises the real in-app update path
(tinyagentos.routes.settings._refresh_graceful_stop_artifacts): starting from the
OLD unit text (with ExecStop=...taos-graceful-stop) and the OLD ungated script
on disk, it asserts the refresh removes ExecStop, installs the gated script, and
that a restart-shaped stop then makes no prepare-shutdown call using the files
the update left behind. This test fails on origin/dev (the refresh step does not
exist there) and passes on the fix branch.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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
    """A throwaway install layout plus a curl stub that records every call.

    ``jobs`` is the output the stub ``systemctl list-jobs`` prints; a real
    shutdown/reboot/halt/poweroff/kexec job makes the script drain, a
    restart-shaped list (no target job) must not.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        script: Path = SCRIPT,
        jobs: str = "80 networking.service start running",
    ) -> None:
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
        # Stub systemctl: systemd queues a shutdown/reboot/halt/poweroff/kexec
        # target job only during a real shutdown. A controller restart has none.
        systemctl = bindir / "systemctl"
        systemctl.write_text("#!/bin/sh\ncat <<'EOF'\n" + jobs + "\nEOF\nexit 0\n")
        systemctl.chmod(0o755)
        self._bindir = bindir
        self._script = script

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
            ["bash", str(self._script)], env=env, capture_output=True, text=True, timeout=60
        )

    @property
    def drain_calls(self) -> list[str]:
        if not self.curl_log.exists():
            return []
        return [ln for ln in self.curl_log.read_text().splitlines() if ln.strip()]


@pytest.fixture
def host(tmp_path: Path):
    return FakeHost(tmp_path)


@pytest.fixture
def reboot_host(tmp_path: Path):
    return FakeHost(tmp_path, jobs="1 reboot.target start")


@pytest.fixture
def poweroff_host(tmp_path: Path):
    return FakeHost(tmp_path, jobs="1 poweroff.target start")


@pytest.fixture
def halt_host(tmp_path: Path):
    return FakeHost(tmp_path, jobs="1 halt.target start")


@pytest.fixture
def shutdown_host(tmp_path: Path):
    return FakeHost(tmp_path, jobs="1 shutdown.target start")


@pytest.fixture
def kexec_host(tmp_path: Path):
    return FakeHost(tmp_path, jobs="1 kexec.target start")


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

    def test_poweroff_stop_drains_exactly_once(self, poweroff_host: FakeHost) -> None:
        result = poweroff_host.run()
        assert result.returncode == 0, result.stderr
        assert len(poweroff_host.drain_calls) == 1, (
            f"poweroff-shaped stop must drain exactly once, got {len(poweroff_host.drain_calls)}: "
            f"{poweroff_host.drain_calls}"
        )

    def test_halt_stop_drains_exactly_once(self, halt_host: FakeHost) -> None:
        result = halt_host.run()
        assert result.returncode == 0, result.stderr
        assert len(halt_host.drain_calls) == 1, (
            f"halt-shaped stop must drain exactly once, got {len(halt_host.drain_calls)}: "
            f"{halt_host.drain_calls}"
        )

    def test_shutdown_stop_drains_exactly_once(self, shutdown_host: FakeHost) -> None:
        result = shutdown_host.run()
        assert result.returncode == 0, result.stderr
        assert len(shutdown_host.drain_calls) == 1, (
            f"shutdown-shaped stop must drain exactly once, got {len(shutdown_host.drain_calls)}: "
            f"{shutdown_host.drain_calls}"
        )

    def test_kexec_stop_drains_exactly_once(self, kexec_host: FakeHost) -> None:
        result = kexec_host.run()
        assert result.returncode == 0, result.stderr
        assert len(kexec_host.drain_calls) == 1, (
            f"kexec-shaped stop must drain exactly once, got {len(kexec_host.drain_calls)}: "
            f"{kexec_host.drain_calls}"
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


class TestGracefulStopArtifactRefresh:
    def test_update_refreshes_old_script_and_unit(self, tmp_path: Path) -> None:
        """The real update-path refresh fixes an install that still has the old
        ungated script and the old unit (with ExecStop): the shipped gated script
        and the ExecStop-free unit are installed, and a restart-shaped stop then
        makes no prepare-shutdown call using the files left behind."""
        from tinyagentos.routes.settings import _refresh_graceful_stop_artifacts

        project = tmp_path / "project"
        (project / "scripts" / "systemd").mkdir(parents=True)
        # Shipped (fixed) script + unit template, as install-server.sh would stage them.
        shutil.copy(SCRIPT, project / "scripts" / "taos-graceful-stop.sh")
        shutil.copy(SYSTEM_UNIT, project / "scripts" / "systemd" / "tinyagentos.service")

        dest = tmp_path / "dest"
        bindir = dest / "usr" / "local" / "bin"
        bindir.mkdir(parents=True)
        unitdir = dest / "etc" / "systemd" / "system"
        unitdir.mkdir(parents=True)
        stop_script_dest = bindir / "taos-graceful-stop"
        unit_dest = unitdir / "tinyagentos.service"

        # OLD ungated script: drains on every stop, no shutdown-job gate.
        old_script = (
            "#!/bin/bash\n"
            "# old: drains unconditionally whenever systemctl is present.\n"
            "if command -v systemctl >/dev/null 2>&1; then\n"
            '  curl -fsS -X POST --max-time 25 "http://localhost:6969/api/system/prepare-shutdown" || true\n'
            "fi\n"
            "exit 0\n"
        )
        stop_script_dest.write_text(old_script)
        stop_script_dest.chmod(0o755)

        # OLD unit: still carries the ExecStop drain line.
        old_unit = (
            "[Unit]\n"
            "Description=TinyAgentOS Controller\n"
            "[Service]\n"
            "User=taos\n"
            "Group=taos\n"
            "WorkingDirectory=/srv/tinyagentos\n"
            "ExecStart=/srv/tinyagentos/.venv/bin/python -m tinyagentos\n"
            "ExecStop=/usr/local/bin/taos-graceful-stop\n"
            "TimeoutStopSec=45\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            "Environment=TAOS_PORT=6969\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        unit_dest.write_text(old_unit)

        # A stub systemctl so daemon-reload inside the refresh succeeds.
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        (fakebin / "systemctl").write_text("#!/bin/sh\nexit 0\n")
        (fakebin / "systemctl").chmod(0o755)
        os.environ["PATH"] = str(fakebin) + os.pathsep + os.environ.get("PATH", "")

        problems = asyncio.run(
            _refresh_graceful_stop_artifacts(
                project, stop_script_dest=stop_script_dest, unit_dest=unit_dest
            )
        )
        assert problems == [], problems

        refreshed_unit = unit_dest.read_text()
        assert not re.search(r"(?m)^\s*ExecStop=", refreshed_unit), (
            f"refreshed unit must not drain:\n{refreshed_unit}"
        )
        refreshed_script = stop_script_dest.read_text()
        assert "shutdown_queued" in refreshed_script
        assert re.search(r"\(shutdown\|reboot\|halt\|poweroff\|kexec\)", refreshed_script)

        # A restart-shaped stop using the refreshed files must not drain.
        restart_host = FakeHost(tmp_path / "run2", script=stop_script_dest)
        result = restart_host.run()
        assert result.returncode == 0, result.stderr
        assert restart_host.drain_calls == [], (
            f"restart-shaped stop must not drain, got {restart_host.drain_calls}"
        )

        # A real poweroff job using the same refreshed files drains exactly once.
        poweroff_host = FakeHost(
            tmp_path / "run3", script=stop_script_dest, jobs="1 poweroff.target start"
        )
        result = poweroff_host.run()
        assert result.returncode == 0, result.stderr
        assert len(poweroff_host.drain_calls) == 1, (
            f"poweroff-shaped stop must drain exactly once, got {len(poweroff_host.drain_calls)}: "
            f"{poweroff_host.drain_calls}"
        )
