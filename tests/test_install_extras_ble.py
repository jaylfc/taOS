import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "lib" / "controller_extras.sh"


def _run_helper(tmp_path, taos_extras_ble=None, handset=True):
    stub = tmp_path / "systemctl"
    if handset:
        stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$1" == "cat" && "$2" == "taos-kiosk.service" ]]; then\n'
            "    exit 0\n"
            "else\n"
            "    exit 1\n"
            "fi\n"
        )
    else:
        stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$1" == "cat" && "$2" == "taos-kiosk.service" ]]; then\n'
            "    exit 1\n"
            "else\n"
            "    exit 1\n"
            "fi\n"
        )
    stub.chmod(0o755)

    script = tmp_path / "run_extras.sh"
    env_line = ""
    if taos_extras_ble is not None:
        env_line = f'export TAOS_EXTRAS_BLE="{taos_extras_ble}"\n'
    script.write_text(
        f'#!/bin/bash\n'
        f'export PATH="{tmp_path}:$PATH"\n'
        f"{env_line}"
        f'source "{HELPER}"\n'
        f'taos_controller_extras\n'
    )
    script.chmod(0o755)

    env = os.environ.copy()
    if taos_extras_ble is not None:
        env["TAOS_EXTRAS_BLE"] = str(taos_extras_ble)
    else:
        env.pop("TAOS_EXTRAS_BLE", None)

    result = subprocess.run([str(script)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_installer_extras_selection_handset(tmp_path):
    assert _run_helper(tmp_path, taos_extras_ble=None, handset=True) == "ble"


def test_installer_extras_selection_non_handset(tmp_path):
    assert _run_helper(tmp_path, taos_extras_ble=None, handset=False) == ""


def test_installer_extras_selection_force_include_on_non_handset(tmp_path):
    assert _run_helper(tmp_path, taos_extras_ble="1", handset=False) == "ble"


def test_installer_extras_selection_force_exclude_on_handset(tmp_path):
    assert _run_helper(tmp_path, taos_extras_ble="0", handset=True) == ""


def test_install_server_inline_matches_lib():
    import re

    script = (REPO_ROOT / "scripts" / "install-server.sh").read_text()
    lib = HELPER.read_text()

    # Extract the entire taos_controller_extras function from both files
    # Use a regex that captures from "taos_controller_extras() {" to the line that is exactly "}"
    # The function has nested braces in the case statement, so we need to handle that
    # We'll use DOTALL to match across lines, and look for the complete function
    script_pattern = r"taos_controller_extras\(\) \{[^}]+\}"
    script_match = re.search(script_pattern, script, re.DOTALL)
    assert script_match, "install-server.sh must inline taos_controller_extras"
    
    lib_pattern = r"taos_controller_extras\(\) \{[^}]+\}"
    lib_match = re.search(lib_pattern, lib, re.DOTALL)
    assert lib_match, "controller_extras.sh must define taos_controller_extras"

    assert script_match.group(0) == lib_match.group(0), (
        "install-server.sh inlined function must match scripts/lib/controller_extras.sh"
    )
