"""Tests for the session installer block that installs every binary the sway config names."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_SESSION = REPO_ROOT / "session" / "install-session.sh"


def _script_text() -> str:
    assert INSTALL_SESSION.exists(), f"{INSTALL_SESSION} is missing"
    return INSTALL_SESSION.read_text()


def _block_3b() -> str:
    """Return the step 3b block from # 3b. to # 3b-end. (inclusive)."""
    # Match from the line starting with "# 3b." up to and including the line "# 3b-end."
    match = re.search(
        r"^# 3b\..*?^# 3b-end\.",
        _script_text(),
        re.DOTALL | re.MULTILINE,
    )
    assert match, "install-session.sh must have a '# 3b.' block ending with '# 3b-end.'"
    return match.group(0)


def _run_block(
    tmp_path: Path,
    *,
    conf_src_override: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the 3b block with the given DESTDIR and optional CONF_SRC override."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()

    # Prepare the script with HERE, CONF_SRC, DESTDIR prepended
    here = REPO_ROOT / "session"
    conf_src = conf_src_override or (here / "etc" / "sway-kiosk.conf")
    destdir = tmp_path

    script_content = (
        "set -eu\n"
        f'HERE="{here}"\n'
        f'CONF_SRC="{conf_src}"\n'
        f'DESTDIR="{destdir}"\n'
        + _block_3b()
    )

    script_path = tmp_path / "block.sh"
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    env = dict(os.environ, PATH=f"{stub_dir}:{os.environ['PATH']}")
    proc = subprocess.run(
        ["bash", str(script_path)], capture_output=True, text=True, env=env, check=False
    )
    return proc


class TestInstallSessionBlock3b:
    def test_block_installs_every_named_binary(self, tmp_path: Path) -> None:
        """The 3b block installs all binaries named in the sway config and the systemd unit."""
        proc = _run_block(tmp_path)
        assert proc.returncode == 0, f"block failed: {proc.stderr}\n{proc.stdout}"

        # Check all expected binaries exist and are executable
        expected_binaries = [
            "taos-shelld",
            "taos-kiosk-bar",
            "taos-shell",
            "taos-app-launch",
            "taos-kiosk-idle",
        ]
        for binary in expected_binaries:
            bin_path = tmp_path / "usr" / "local" / "bin" / binary
            assert bin_path.exists(), f"{bin_path} does not exist"
            assert os.access(bin_path, os.X_OK), f"{bin_path} is not executable"

        # Check systemd unit exists
        unit_path = tmp_path / "etc" / "systemd" / "system" / "taos-kiosk.service"
        assert unit_path.exists(), f"{unit_path} does not exist"

    def test_missing_named_binary_fails(self, tmp_path: Path) -> None:
        """A config naming a non-installed binary causes the block to fail."""
        # Copy the config and append a reference to a non-existent helper
        conf_src = tmp_path / "sway-kiosk.conf"
        original = (REPO_ROOT / "session" / "etc" / "sway-kiosk.conf").read_text()
        conf_src.write_text(original + "\nexec_always /usr/local/bin/taos-nosuch-helper\n")

        proc = _run_block(tmp_path, conf_src_override=conf_src)
        assert proc.returncode != 0, "block should have failed for missing binary"
        assert "taos-nosuch-helper" in proc.stdout, (
            f"stdout should mention missing binary; got:\n{proc.stdout}"
        )