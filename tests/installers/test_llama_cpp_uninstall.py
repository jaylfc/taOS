import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "uninstall-llama-cpp.sh"


def _write_stub(bin_dir: Path, name: str) -> None:
    """Write a PATH stub that records its argv (command + args) to
    TAOS_STUB_LOG and exits 0."""
    stub = bin_dir / name
    stub.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$(basename \"$0\") $*\" >> \"$TAOS_STUB_LOG\"\nexit 0\n"
    )
    stub.chmod(0o755)


class TestUninstallLlamaCppScript:
    def test_systemd_branch_calls_disable_now_and_daemon_reload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        systemd_dir = tmp_path / "systemd"
        systemd_dir.mkdir()
        initd_dir = tmp_path / "initd"
        initd_dir.mkdir()
        call_log = tmp_path / "calls.log"

        _write_stub(bin_dir, "systemctl")
        # unit file is "installed" so the teardown path runs
        (systemd_dir / "llama-cpp.service").write_text("[Unit]\n")

        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["TAOS_INIT_SYSTEM"] = "systemd"
        env["TAOS_SYSTEMD_DIR"] = str(systemd_dir)
        env["TAOS_INITD_DIR"] = str(initd_dir)
        env["TAOS_STUB_LOG"] = str(call_log)
        env["TAOS_SUDO"] = ""

        subprocess.run(
            ["bash", str(SCRIPT), str(tmp_path)],
            env=env,
            check=True,
            capture_output=True,
        )

        calls = call_log.read_text().splitlines() if call_log.exists() else []
        assert "systemctl disable --now llama-cpp.service" in calls
        assert "systemctl daemon-reload" in calls
        assert not (systemd_dir / "llama-cpp.service").exists()

    def test_openrc_branch_calls_stop_del_and_rm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        systemd_dir = tmp_path / "systemd"
        systemd_dir.mkdir()
        initd_dir = tmp_path / "initd"
        initd_dir.mkdir()
        call_log = tmp_path / "calls.log"

        _write_stub(bin_dir, "rc-service")
        _write_stub(bin_dir, "rc-update")
        # init script is "installed" so the teardown path runs
        (initd_dir / "llama-cpp").write_text("#!/bin/sh\n")

        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["TAOS_INIT_SYSTEM"] = "openrc"
        env["TAOS_SYSTEMD_DIR"] = str(systemd_dir)
        env["TAOS_INITD_DIR"] = str(initd_dir)
        env["TAOS_STUB_LOG"] = str(call_log)
        env["TAOS_SUDO"] = ""

        subprocess.run(
            ["bash", str(SCRIPT), str(tmp_path)],
            env=env,
            check=True,
            capture_output=True,
        )

        calls = call_log.read_text().splitlines() if call_log.exists() else []
        assert "rc-service llama-cpp stop" in calls
        assert "rc-update del llama-cpp default" in calls
        assert not (initd_dir / "llama-cpp").exists()

    def test_launchd_branch_boots_out_and_removes_plist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        agents_dir = tmp_path / "LaunchAgents"
        agents_dir.mkdir()
        call_log = tmp_path / "calls.log"

        _write_stub(bin_dir, "launchctl")
        # plist is "installed" so the teardown path runs
        (agents_dir / "com.taos.llama-cpp.plist").write_text("<?xml version=\"1.0\"?>\n")

        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["TAOS_INIT_SYSTEM"] = "launchd"
        env["TAOS_LAUNCHAGENTS_DIR"] = str(agents_dir)
        env["TAOS_STUB_LOG"] = str(call_log)
        env["TAOS_SUDO"] = ""

        subprocess.run(
            ["bash", str(SCRIPT), str(tmp_path)],
            env=env,
            check=True,
            capture_output=True,
        )

        calls = call_log.read_text().splitlines() if call_log.exists() else []
        # Check for launchctl bootout call
        bootout_calls = [c for c in calls if c.startswith("launchctl bootout gui/") and c.endswith("/com.taos.llama-cpp")]
        assert bootout_calls, f"Expected launchctl bootout call, got calls: {calls}"
        # plist should be removed
        assert not (agents_dir / "com.taos.llama-cpp.plist").exists()

    def test_systemd_unremovable_unit_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        systemd_dir = tmp_path / "systemd"
        systemd_dir.mkdir()
        initd_dir = tmp_path / "initd"
        initd_dir.mkdir()
        call_log = tmp_path / "calls.log"

        # systemctl stub that succeeds
        _write_stub(bin_dir, "systemctl")
        # rm stub that fails (exits 1 without deleting)
        rm_stub = bin_dir / "rm"
        rm_stub.write_text("#!/usr/bin/env bash\nexit 1\n")
        rm_stub.chmod(0o755)

        # unit file is "installed" so the teardown path runs
        (systemd_dir / "llama-cpp.service").write_text("[Unit]\n")

        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["TAOS_INIT_SYSTEM"] = "systemd"
        env["TAOS_SYSTEMD_DIR"] = str(systemd_dir)
        env["TAOS_INITD_DIR"] = str(initd_dir)
        env["TAOS_STUB_LOG"] = str(call_log)
        env["TAOS_SUDO"] = ""

        result = subprocess.run(
            ["bash", str(SCRIPT), str(tmp_path)],
            env=env,
            capture_output=True,
        )

        assert result.returncode != 0, f"Expected non-zero exit, got {result.returncode}"
        stdout = result.stdout.decode()
        assert "torn down" not in stdout, f"Should not log 'torn down' on failure, got: {stdout}"

    def test_sudo_prefix_used_when_not_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        if os.geteuid() == 0:
            pytest.skip("Test requires non-root user")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        systemd_dir = tmp_path / "systemd"
        systemd_dir.mkdir()
        initd_dir = tmp_path / "initd"
        initd_dir.mkdir()
        call_log = tmp_path / "calls.log"

        # sudo stub that records argv and then execs the remaining args after -n
        sudo_stub = bin_dir / "sudo"
        sudo_stub.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"sudo $*\" >> \"$TAOS_STUB_LOG\"\n"
            "shift\n"
            "exec \"$@\"\n"
        )
        sudo_stub.chmod(0o755)

        _write_stub(bin_dir, "systemctl")
        (systemd_dir / "llama-cpp.service").write_text("[Unit]\n")

        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["TAOS_INIT_SYSTEM"] = "systemd"
        env["TAOS_SYSTEMD_DIR"] = str(systemd_dir)
        env["TAOS_INITD_DIR"] = str(initd_dir)
        env["TAOS_STUB_LOG"] = str(call_log)
        # TAOS_SUDO unset so it defaults to "sudo -n"

        subprocess.run(
            ["bash", str(SCRIPT), str(tmp_path)],
            env=env,
            check=True,
            capture_output=True,
        )

        calls = call_log.read_text().splitlines() if call_log.exists() else []
        # Check that sudo -n is used for systemctl disable --now
        assert any("sudo -n systemctl disable --now llama-cpp.service" in c for c in calls), \
            f"Expected 'sudo -n systemctl disable --now llama-cpp.service' in calls, got: {calls}"