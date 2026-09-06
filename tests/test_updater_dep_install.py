"""The in-app updater dependency step must install exactly what CI tested.

These tests pin the behaviour of the dependency-install helpers used by the
Settings "Install Update" flow:

  * `_find_uv` resolves uv robustly (uv is not always on PATH; on the Pi it
    lives at <install_dir>/.local/bin/uv).
  * `_install_dependencies` prefers a lockfile-pinned `uv sync --frozen
    --extra proxy` and falls back to `pip install -e .[proxy]` only when uv is
    absent. The proxy extra (litellm + prisma) is a core dep, so both paths
    carry it to match install-server.sh.
  * a non-zero install return code aborts the update WITHOUT writing the
    pending-restart marker (the crash-loop safety net stays intact).

All subprocess execution is mocked; nothing actually runs uv/pip/git.
"""

import shutil
from pathlib import Path

import pytest

from tinyagentos.routes import settings as settings_mod


# --- _find_uv resolution order -------------------------------------------

def test_find_uv_prefers_path(monkeypatch):
    """(a) shutil.which wins when uv is on PATH."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    # Should never reach the filesystem candidates.
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
    """uv found -> runs `uv sync --frozen --extra proxy` with cwd + HOME=project_dir."""
    project = Path("/srv/taos")
    monkeypatch.setattr(settings_mod, "_find_uv", lambda pd: "/opt/uv")

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return 0, "synced"

    monkeypatch.setattr(settings_mod, "_run_capture", fake_run)

    rc, out = await settings_mod._install_dependencies(project)

    assert rc == 0
    assert out == "synced"
    assert captured["cmd"] == ["/opt/uv", "sync", "--frozen", "--extra", "proxy"]
    assert captured["cwd"] == str(project)
    assert captured["env"]["HOME"] == str(project)


@pytest.mark.asyncio
async def test_install_falls_back_to_pip(monkeypatch):
    """uv absent -> runs `pip install -e .[proxy]` (legacy path)."""
    project = Path("/srv/taos")
    monkeypatch.setattr(settings_mod, "_find_uv", lambda pd: None)
    # No venv pip on disk -> bare "pip".
    monkeypatch.setattr(Path, "exists", lambda self: False)

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return 0, "installed"

    monkeypatch.setattr(settings_mod, "_run_capture", fake_run)

    rc, out = await settings_mod._install_dependencies(project)

    assert rc == 0
    assert captured["cmd"] == ["pip", "install", "-e", ".[proxy]"]
    assert captured["cwd"] == str(project)
    # pip path inherits the parent env (no override).
    assert captured["env"] is None


@pytest.mark.asyncio
async def test_install_uses_venv_pip_when_present(monkeypatch):
    """uv absent + .venv present -> uses the venv pip binary."""
    project = Path("/srv/taos")
    venv_pip = project / ".venv" / "bin" / "pip"
    monkeypatch.setattr(settings_mod, "_find_uv", lambda pd: None)
    monkeypatch.setattr(Path, "exists", lambda self: self == venv_pip)

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        return 0, ""

    monkeypatch.setattr(settings_mod, "_run_capture", fake_run)

    await settings_mod._install_dependencies(project)
    assert captured["cmd"] == [str(venv_pip), "install", "-e", ".[proxy]"]


def test_updater_extras_match_install_server():
    """The updater's UPDATE_EXTRAS must equal install-server.sh's pip extras.

    Both install paths carry the same optional extras; if install-server.sh
    later adds one (e.g. `.[proxy,gpu]`) the updater must too, or a bare-set
    `uv sync --frozen` will strip it on the next update. This binds the two
    so a drift fails CI instead of silently breaking a live box.
    """
    import re

    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts" / "install-server.sh").read_text()
    matches = re.findall(r'pip install[^\n]*-e\s+["\']?\.\[([^\]]+)\]', script)
    assert matches, "could not find a `pip install -e .[extras]` line in install-server.sh"
    script_extras = {e.strip() for e in matches[-1].split(",") if e.strip()}
    assert script_extras == set(settings_mod.UPDATE_EXTRAS), (
        f"install-server.sh installs extras {script_extras} but the updater installs "
        f"{set(settings_mod.UPDATE_EXTRAS)}; keep them in parity (settings.UPDATE_EXTRAS)"
    )


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

    rc, out = await settings_mod._pip_rebuild_restart(project, "deadbeef")

    assert rc == 1
    assert out == "install boom"
    assert wrote["called"] is False
