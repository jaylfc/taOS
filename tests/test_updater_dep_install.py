"""The in-app updater dependency step must install exactly what CI tested.

These tests pin the behaviour of the dependency-install helpers used by the
Settings "Install Update" flow:

  * `_find_uv` resolves uv robustly (uv is not always on PATH; on the Pi it
    lives at <install_dir>/.local/bin/uv).
  * `_install_dependencies` prefers a lockfile-pinned `uv sync --frozen
    --extra ...` with the device's extras (none by default; ``ble`` on a
    handset) and falls back to `pip install -e .` only when uv is absent. Both paths
    carry it to match install-server.sh.
  * a non-zero install return code aborts the update WITHOUT writing the
    pending-restart marker (the crash-loop safety net stays intact).

All subprocess execution is mocked; nothing actually runs uv/pip/git.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tinyagentos.routes import settings as settings_mod


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "lib" / "controller_extras.sh"


# --- _find_uv resolution order -------------------------------------------

def test_find_uv_prefers_path(monkeypatch):
    """(a) shutil.which wins when uv is on PATH."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(Path, "exists", lambda self: pytest.fail("must not stat"))
    assert settings_mod._find_uv(Path("/srv/taos")) == "/usr/bin/uv"


def test_find_uv_project_local_bin(monkeypatch):
    """(b) <project_dir>/.local/bin/uv is used when not on PATH."""
    project = Path("/srv/taos")
    expected = project / ".local" / "bin" / "uv"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "exists", lambda self: self == expected)
    monkeypatch.setattr(settings_mod.os, "access", lambda p, mode: True)
    assert settings_mod._find_uv(project) == str(expected)


def test_find_uv_not_found_returns_none(monkeypatch):
    """uv absent everywhere -> None so the caller falls back to pip."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(settings_mod.os, "access", lambda p, mode: True)
    assert settings_mod._find_uv(Path("/srv/taos")) is None


# --- _install_dependencies dispatch --------------------------------------

@pytest.mark.asyncio
async def test_install_uses_uv_sync_frozen(monkeypatch):
    """uv found -> runs `uv sync --frozen` (no extra off a handset) with cwd + HOME=project_dir."""
    monkeypatch.setattr(settings_mod, "_detect_device_class", lambda: None)
    monkeypatch.setattr(settings_mod, "_find_uv", lambda pd: "/opt/uv")

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return 0, "synced"

    monkeypatch.setattr(settings_mod, "_run_capture", fake_run)

    rc, out = await settings_mod._install_dependencies(Path("/srv/taos"))

    assert rc == 0
    assert out == "synced"
    assert captured["cmd"] == ["/opt/uv", "sync", "--frozen"]
    assert captured["cwd"] == "/srv/taos"
    assert captured["env"]["HOME"] == "/srv/taos"


@pytest.mark.asyncio
async def test_install_falls_back_to_pip(monkeypatch):
    """uv absent -> runs `pip install -e .` (legacy path; `.[]` is not a pip target)."""
    monkeypatch.setattr(settings_mod, "_detect_device_class", lambda: None)
    monkeypatch.setattr(settings_mod, "_find_uv", lambda pd: None)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return 0, "installed"

    monkeypatch.setattr(settings_mod, "_run_capture", fake_run)

    rc, out = await settings_mod._install_dependencies(Path("/srv/taos"))

    assert rc == 0
    assert captured["cmd"] == ["pip", "install", "-e", "."]
    assert captured["cwd"] == "/srv/taos"
    assert captured["env"] is None


@pytest.mark.asyncio
async def test_install_uses_venv_pip_when_present(monkeypatch):
    """uv absent + .venv present -> uses the venv pip binary."""
    monkeypatch.setattr(settings_mod, "_detect_device_class", lambda: None)
    monkeypatch.setattr(settings_mod, "_find_uv", lambda pd: None)

    project = Path("/srv/taos")
    venv_pip = project / ".venv" / "bin" / "pip"
    monkeypatch.setattr(Path, "exists", lambda self: self == venv_pip)

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        return 0, ""

    monkeypatch.setattr(settings_mod, "_run_capture", fake_run)

    await settings_mod._install_dependencies(project)
    assert captured["cmd"] == [str(venv_pip), "install", "-e", "."]


# --- parity: Python selection must match shell helper ---------------------

def _shell_extras(tmp_path, taos_extras_ble=None, handset=True):
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


def test_updater_extras_parity_with_shell_helper(tmp_path, monkeypatch):
    """Python _compute_update_extras matches the shell helper for identical inputs."""
    cases = [
        ("mobile", None, "ble"),
        (None, None, ""),
        (None, "1", "ble"),
        ("mobile", "0", ""),
    ]

    for device_class, taos_ble, expected in cases:
        monkeypatch.setattr(settings_mod, "_detect_device_class", lambda dc=device_class: dc)
        if taos_ble is not None:
            monkeypatch.setenv("TAOS_EXTRAS_BLE", taos_ble)
        else:
            monkeypatch.delenv("TAOS_EXTRAS_BLE", raising=False)

        python_extras = settings_mod._compute_update_extras()
        shell_extras = _shell_extras(tmp_path, taos_extras_ble=taos_ble, handset=(device_class == "mobile"))
        assert tuple(python_extras) == tuple(e for e in shell_extras.split(",") if e), (
            f"mismatch for device_class={device_class!r} TAOS_EXTRAS_BLE={taos_ble!r}: "
            f"python={python_extras} shell={shell_extras}"
        )


def test_updater_extras_match_install_server():
    """The updater's UPDATE_EXTRAS must equal install-server.sh's pip extras.

    Both install paths carry the same optional extras; if install-server.sh
    later adds one (e.g. `.[proxy,gpu]`) the updater must too, or a bare-set
    `uv sync --frozen` will strip it on the next update. This binds the two
    so a drift fails CI instead of silently breaking a live box.

    If install-server.sh uses a dynamic extras selection (e.g.
    `$(taos_controller_extras)`), the test verifies that the function exists
    and that the script's extras-selection logic matches the updater's
    device-class-based selection.
    """
    import re

    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts" / "install-server.sh").read_text()
    
    # Find the taos_controller_extras function in install-server.sh
    script_match = re.search(r'taos_controller_extras\(\) \{[^}]+\}', script, re.DOTALL)
    assert script_match, "install-server.sh must inline taos_controller_extras"
    
    # Find the taos_controller_extras function in the lib
    lib = repo_root / "scripts" / "lib" / "controller_extras.sh"
    lib_match = re.search(r'taos_controller_extras\(\) \{[^}]+\}', lib.read_text(), re.DOTALL)
    assert lib_match, "controller_extras.sh must define taos_controller_extras"
    
    # Assert the inlined function equals the lib's
    assert script_match.group(0) == lib_match.group(0), (
        "install-server.sh inlined function must match scripts/lib/controller_extras.sh"
    )
    
    # The installer's final pip line must take its extras from the function,
    # bracketed only when non-empty (`.[]` is not a valid pip target).
    assert '_taos_extras="$(taos_controller_extras)"' in script, (
        "install-server.sh must take its extras from $(taos_controller_extras)"
    )
    pip_lines = re.findall(r'^\./\.venv/bin/pip install[^\n]*-e[^\n]*$', script, re.MULTILINE)
    assert pip_lines, "could not find the editable `pip install -e` line in install-server.sh"
    assert pip_lines[-1].endswith('-e ".${_taos_extras:+[$_taos_extras]}"'), pip_lines[-1]


@pytest.mark.asyncio
async def test_install_nonzero_rc_is_propagated(monkeypatch):
    """A failed install returns (rc, output) so the caller aborts safely."""
    project = Path("/srv/taos")
    monkeypatch.setattr(settings_mod, "_find_uv", lambda pd: "/opt/uv")

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        return 7, "uv: lockfile out of date"

    monkeypatch.setattr(settings_mod, "_run_capture", fake_run)

    rc, out = await settings_mod._install_dependencies(project)
    assert rc == 7
    assert "lockfile out of date" in out


# --- abort ordering: failed install must NOT write pending restart -------

@pytest.mark.asyncio
async def test_failed_install_does_not_write_pending_restart(monkeypatch):
    """The crash-loop safety net: if the dep install fails, the update aborts
    and write_pending_restart is never called."""
    project = Path("/srv/taos")

    async def failing_install(pd):
        return 1, "install boom"

    monkeypatch.setattr(settings_mod, "_install_dependencies", failing_install)

    wrote = {"called": False}

    def fake_write(sha):
        wrote["called"] = True

    monkeypatch.setattr(settings_mod, "write_pending_restart", fake_write)

    rc, out, warning = await settings_mod._pip_rebuild_restart(project, "deadbeef")

    assert rc == 1
    assert out == "install boom"
    assert warning is None
    assert wrote["called"] is False
