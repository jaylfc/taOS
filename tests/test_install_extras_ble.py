"""Tests for taos_controller_extras using the real script with a stub systemctl."""
import pytest
import os
import subprocess
import tempfile
from pathlib import Path


def _make_stub_systemctl(tmp_path, exit_code):
    """Create a stub systemctl script that returns the given exit code."""
    script = tmp_path / "systemctl"
    script.write_text("#!/usr/bin/env bash\nexit " + str(exit_code))
    script.chmod(0o755)


def _run_controller_extras(stub_exit, taos_extras_ble=None):
    """Run taos_controller_extras via the real script with a stub systemctl."""
    repo_root = Path(__file__).resolve().parents[1]
    lib_dir = repo_root / "scripts" / "lib"
    lib_file = lib_dir / "controller_extras.sh"

    with tempfile.TemporaryDirectory() as tmp_path:
        _make_stub_systemctl(Path(tmp_path), stub_exit)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(tmp_path) + os.pathsep + old_path

        try:
            env = os.environ.copy()
            if taos_extras_ble is not None:
                env["TAOS_EXTRAS_BLE"] = str(taos_extras_ble)

            # Source the real controller_extras.sh and call the function
            result = subprocess.run(
                ["bash", "-c",
                 "source \"" + str(lib_file) + "\"; taos_controller_extras"],
                capture_output=True,
                text=True,
                env=env,
            )
            return result.stdout.strip()
        finally:
            os.environ["PATH"] = old_path


@pytest.mark.parametrize("stub_exit, taos_extras_ble, expected", [
    # Case 1: stub exit 0 -> handset detected -> proxy,ble
    (0, None, "proxy,ble"),
    # Case 2: stub exit 1 -> not handset -> proxy
    (1, None, "proxy"),
    # Case 3: TAOS_EXTRAS_BLE=1 + stub exit 1 -> proxy,ble
    (1, 1, "proxy,ble"),
    # Case 4: TAOS_EXTRAS_BLE=0 + stub exit 0 -> proxy (ble excluded even on handset)
    (0, 0, "proxy"),
])
def test_taos_controller_extras_cases(stub_exit, taos_extras_ble, expected):
    """Real taos_controller_extras with stub systemctl - four cases."""
    result = _run_controller_extras(stub_exit, taos_extras_ble)
    assert result == expected, f"Expected '{expected}' but got '{result}'"