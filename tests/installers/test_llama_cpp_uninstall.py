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
