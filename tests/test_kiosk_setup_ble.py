import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _extract_ble_block():
    """Extract the BLE installation block from kiosk-setup.sh by line number."""
    lines = (REPO_ROOT / "scripts" / "kiosk-setup.sh").read_text().splitlines()
    # Lines 170-198 (1-indexed) contain the BLE block
    return "\n".join(lines[169:198])


def _run_ble_block(tmp_path, venv_owner=None, taos_user=None):
    """Run the BLE block from kiosk-setup.sh with stubs."""
    taos_dir = tmp_path / "taos"
    venv_bin = taos_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)

    pip_args_file = tmp_path / "pip_args.txt"
    sudo_args_file = tmp_path / "sudo_args.txt"

    pip_stub = venv_bin / "pip"
    pip_stub.write_text(
        f'#!/bin/bash\n'
        f'printf "%s\\n" "$@" > "{pip_args_file}"\n'
        f'exit 0\n'
    )
    pip_stub.chmod(0o755)

    stubs = tmp_path / "stubs"
    stubs.mkdir()

    (stubs / "systemctl").write_text("#!/bin/bash\nexit 0\n")
    (stubs / "systemctl").chmod(0o755)

    if venv_owner is not None:
        (stubs / "stat").write_text(
            f'#!/bin/bash\n'
            f'if [[ "$1" == "-c" && "$2" == "%U" ]]; then\n'
            f'    echo "{venv_owner}"\n'
            f'else\n'
            f'    exit 0\n'
            f'fi\n'
        )
    else:
        (stubs / "stat").write_text("#!/bin/bash\nexit 0\n")
    (stubs / "stat").chmod(0o755)

    (stubs / "sudo").write_text(
        f'#!/bin/bash\n'
        f'printf "%s\\n" "$@" > "{sudo_args_file}"\n'
        f'exit 0\n'
    )
    (stubs / "sudo").chmod(0o755)

    (stubs / "whoami").write_text('#!/bin/bash\necho "currentuser"\nexit 0\n')
    (stubs / "whoami").chmod(0o755)

    for cmd in ["apt-get", "getent", "groupadd", "usermod", "id", "chmod"]:
        stub = stubs / cmd
        stub.write_text("#!/bin/bash\nexit 0\n")
        stub.chmod(0o755)

    env_lines = []
    if taos_user is not None:
        env_lines.append(f'export TAOS_USER="{taos_user}"')
    else:
        env_lines.append("unset TAOS_USER")

    ble_block = _extract_ble_block()

    script = tmp_path / "run_ble.sh"
    script.write_text(
        f'#!/bin/bash\n'
        f'set -e\n'
        f'export PATH="{stubs}:$PATH"\n'
        f'export TAOS_DIR="{taos_dir}"\n'
        f'export PIP_ARGS_FILE="{pip_args_file}"\n'
        f'export SUDO_ARGS_FILE="{sudo_args_file}"\n'
        f'{chr(10).join(env_lines)}\n'
        f'unset TAOS_EXTRAS_BLE\n'
        f'\n'
        f'{ble_block}\n'
    )
    script.chmod(0o755)

    env = os.environ.copy()
    env.pop("TAOS_EXTRAS_BLE", None)
    if taos_user is not None:
        env["TAOS_USER"] = taos_user
    else:
        env.pop("TAOS_USER", None)

    result = subprocess.run([str(script)], capture_output=True, text=True, env=env)
    return result


def test_kiosk_ble_block_default_path(tmp_path):
    """Default path: TAOS_EXTRAS_BLE unset, venv pip present -> exit 0, pip called with [ble]."""
    result = _run_ble_block(tmp_path, taos_user="currentuser")
    assert result.returncode == 0, f"stderr: {result.stderr}"

    pip_args_file = tmp_path / "pip_args.txt"
    assert pip_args_file.exists()
    args = pip_args_file.read_text().strip().split("\n")
    assert "install" in args
    assert "--quiet" in args
    assert "-e" in args
    assert any("[ble]" in arg for arg in args)


def test_kiosk_ble_block_sudo_when_venv_owner_differs(tmp_path):
    """venv_owner != current user and TAOS_USER unset -> pip invoked via sudo -u <owner>."""
    result = _run_ble_block(tmp_path, venv_owner="venvuser")
    assert result.returncode == 0, f"stderr: {result.stderr}"

    sudo_args_file = tmp_path / "sudo_args.txt"
    assert sudo_args_file.exists()
    sudo_args = sudo_args_file.read_text().strip().split("\n")
    assert sudo_args[0] == "-u"
    assert sudo_args[1] == "venvuser"
    assert any("[ble]" in arg for arg in sudo_args)
