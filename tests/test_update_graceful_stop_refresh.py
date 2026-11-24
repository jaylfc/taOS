"""The update refreshes the installed graceful-stop files and the drain
decision is the controller's (tsk-72lu32, fix-forward of #4465).

Everything here goes through the real routes (/api/settings/update,
/api/settings/update-channel, /api/system/prepare-shutdown) or the real
lifespan. Only the boundaries are faked: the filesystem is a temp tree (see the
autouse isolation fixture in conftest.py), systemctl is a recorder, git/pip are
the same stand-ins the other update-route tests use.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import tinyagentos.routes.settings as settings_mod
from tinyagentos.update_runner import UpdateResult

PROJECT = Path(settings_mod.__file__).resolve().parents[2]
SHIPPED_SCRIPT = (PROJECT / "scripts" / "taos-graceful-stop.sh").read_bytes()

OLD_SCRIPT = b"#!/bin/bash\n# old: drains on every stop\ncurl -X POST http://localhost:6969/api/system/prepare-shutdown\n"

# What an install made by the old install-server.sh looks like, plus what the
# person added to it by hand: an extra Environment line, a second ExecStop and
# a comment. None of that may be lost.
CUSTOM_UNIT = """\
[Unit]
Description=TinyAgentOS Controller
# my note: keep this unit on the fast disk
After=network-online.target

[Service]
User=taos
WorkingDirectory={project}
ExecStart={project}/.venv/bin/python -m tinyagentos
ExecStop=/usr/local/bin/taos-graceful-stop
ExecStop=/usr/local/bin/my-own-cleanup --quiet
Environment=PYTHONUNBUFFERED=1
Environment=TAOS_PORT=7001
Environment=MY_CUSTOM_SETTING=yes

[Install]
WantedBy=multi-user.target
"""


class Systemctl:
    """Records every systemctl call the update path makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.code = 0
        self.err = ""

    async def __call__(self, *args: str):
        self.calls.append(args)
        return self.code, self.err


@pytest.fixture
def systemctl(monkeypatch) -> Systemctl:
    rec = Systemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", rec)
    return rec


@pytest.fixture
def system_install():
    """An existing system install carrying the old unit and the old script."""
    unit = settings_mod._SYSTEM_UNIT
    script = settings_mod._SYSTEM_STOP_SCRIPT
    unit.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    unit.write_text(CUSTOM_UNIT.format(project=PROJECT))
    script.write_bytes(OLD_SCRIPT)
    script.chmod(0o755)
    return unit, script


def _update_patches(restarts: list):
    """The stand-ins every update-route test uses for git, pip and the bundle."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"Already up to date.\n", b""))

    async def fake_restart(_app_state):
        restarts.append(True)

    return (
        patch("tinyagentos.routes.settings.asyncio.create_subprocess_exec", return_value=fake_proc),
        patch("tinyagentos.routes.settings._run_capture", new=AsyncMock(return_value=(0, "ok"))),
        patch("tinyagentos.routes.settings._stash_local_source_changes", new=AsyncMock(return_value=False)),
        patch("tinyagentos.routes.settings._update_local_taosmd", new=AsyncMock(return_value={})),
        patch(
            "tinyagentos.desktop_rebuild.rebuild_desktop_bundle_if_stale",
            new=AsyncMock(return_value=MagicMock(rebuilt=False, success=True, message="current")),
        ),
        patch("tinyagentos.routes.system._do_restart", new=fake_restart),
        patch("tinyagentos.routes.settings.write_pending_restart"),
        patch("tinyagentos.update_preflight.check_preflight", return_value=[]),
    )


async def _post_update(client, restarts: list):
    patches = _update_patches(restarts)
    for p in patches:
        p.start()
    try:
        return await client.post("/api/settings/update")
    finally:
        for p in reversed(patches):
            p.stop()


async def _post_channel(client, monkeypatch, restarts: list):
    async def lsremote(project_dir):
        return ["master", "dev"]

    async def resolve(store, project_dir):
        return "master"

    async def switch(branch, project_dir, gpg_fingerprint=None, gpg_required=False):
        return UpdateResult(previous_sha="aaa", new_sha="bbb", recovery_tag="tag1", message="ok")

    async def rebuild(project_dir, target_sha):
        return 0, "", None

    async def fake_restart(_app_state):
        restarts.append(True)

    # desktop_settings is lifespan-only; the route saves the tracked branch.
    store = client._transport.app.state.desktop_settings
    if store._db is None:
        await store.init()
    monkeypatch.setattr(settings_mod, "_remote_branches", lsremote)
    monkeypatch.setattr(settings_mod, "resolve_tracked_branch", resolve)
    monkeypatch.setattr(settings_mod, "snapshot_data_dir", lambda d: None)
    monkeypatch.setattr(settings_mod, "switch_to_branch", switch)
    monkeypatch.setattr(settings_mod, "_pip_rebuild_restart", rebuild)
    monkeypatch.setattr("tinyagentos.routes.system._do_restart", fake_restart)
    return await client.post("/api/settings/update-channel", json={"branch": "dev"})


def _dropin_of(unit: Path) -> Path:
    return unit.parent / (unit.name + ".d") / settings_mod._DROPIN_NAME


# --------------------------------------------------------------------------
# /api/settings/update on an existing, customised install
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_keeps_a_customised_unit_and_clears_the_drain_in_a_dropin(
    client, system_install, systemctl,
):
    unit, script = system_install
    original_unit = unit.read_text()
    restarts: list = []

    resp = await _post_update(client, restarts)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "restarting"
    assert restarts == [True]
    # The person's own unit is not opened for writing: every customisation lives.
    assert unit.read_text() == original_unit
    # The drain is cleared in a drop-in, with the person's other ExecStop kept.
    dropin = _dropin_of(unit).read_text()
    assert "ExecStop=\n" in dropin
    assert "ExecStop=/usr/local/bin/my-own-cleanup --quiet" in dropin
    assert "ExecStop=/usr/local/bin/taos-graceful-stop" not in dropin
    # The stop script is the shipped one; the old one is kept beside it.
    assert script.read_bytes() == SHIPPED_SCRIPT
    assert (script.parent / (script.name + ".bak")).read_bytes() == OLD_SCRIPT
    # systemd re-reads its files, and that is ALL it is asked to do.
    assert systemctl.calls == [("daemon-reload",)]
    # The update result says what was done.
    assert data["graceful_stop_refresh"]["scope"] == "system"
    assert str(_dropin_of(unit)) in data["graceful_stop_refresh"]["changed"]
    assert data["graceful_stop_refresh"]["problems"] == []


@pytest.mark.asyncio
async def test_update_refresh_is_idempotent(client, system_install, systemctl):
    unit, script = system_install
    await _post_update(client, [])
    systemctl.calls.clear()
    backup = script.parent / (script.name + ".bak")
    snapshot = (_dropin_of(unit).read_text(), script.read_bytes(), backup.read_bytes())

    resp = await _post_update(client, [])

    assert resp.status_code == 200, resp.text
    assert resp.json()["graceful_stop_refresh"]["changed"] == []
    assert systemctl.calls == [], "nothing changed, so systemd is not asked to reload"
    assert snapshot == (_dropin_of(unit).read_text(), script.read_bytes(), backup.read_bytes())


@pytest.mark.asyncio
async def test_update_on_a_unit_without_the_proxy_port_does_not_crash(
    client, system_install, systemctl,
):
    """The first version of this refresh read the browser-proxy port out of the
    unit and raised NameError when the unit lacked it: a 500 after the merge,
    before the restart."""
    unit, _ = system_install
    assert "TAOS_BROWSER_PROXY_PORT" not in unit.read_text()
    restarts: list = []

    resp = await _post_update(client, restarts)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "restarting"
    assert restarts == [True]


@pytest.mark.asyncio
async def test_update_reports_an_unwritable_destination_and_still_restarts(
    client, system_install, systemctl,
):
    unit, script = system_install
    # A FILE where the drop-in directory must go: mkdir fails, as it does for
    # a read-only /etc.
    (unit.parent / (unit.name + ".d")).write_text("in the way")
    restarts: list = []

    resp = await _post_update(client, restarts)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "restarting" and restarts == [True]
    problems = data["graceful_stop_refresh"]["problems"]
    assert len(problems) == 1 and "cannot" in problems[0], problems
    assert "Graceful-stop refresh:" in data["message"]
    assert problems[0] in data["message"]
    # The script part still went through.
    assert script.read_bytes() == SHIPPED_SCRIPT


@pytest.mark.asyncio
async def test_update_reports_a_failed_daemon_reload(client, system_install, systemctl):
    systemctl.code, systemctl.err = 1, "Access denied"
    resp = await _post_update(client, [])
    assert resp.status_code == 200, resp.text
    problems = resp.json()["graceful_stop_refresh"]["problems"]
    assert len(problems) == 1 and "daemon-reload" in problems[0] and "Access denied" in problems[0]


@pytest.mark.asyncio
async def test_a_bug_in_the_refresh_is_reported_and_does_not_stop_the_restart(
    client, system_install, systemctl, monkeypatch,
):
    async def boom(project_dir):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(settings_mod, "_refresh_graceful_stop_artifacts", boom)
    restarts: list = []

    resp = await _post_update(client, restarts)

    assert resp.status_code == 200, resp.text
    assert restarts == [True]
    problems = resp.json()["graceful_stop_refresh"]["problems"]
    assert problems and "RuntimeError: unexpected" in problems[0]


@pytest.mark.asyncio
async def test_update_leaves_a_unit_of_another_checkout_alone(client, systemctl):
    unit = settings_mod._SYSTEM_UNIT
    unit.parent.mkdir(parents=True)
    unit.write_text(CUSTOM_UNIT.format(project="/srv/some-other-checkout"))
    resp = await _post_update(client, [])
    assert resp.status_code == 200, resp.text
    assert resp.json()["graceful_stop_refresh"] == {"scope": None, "changed": [], "problems": []}
    assert not _dropin_of(unit).exists()
    assert systemctl.calls == []


@pytest.mark.asyncio
async def test_update_on_a_user_install_reloads_the_user_manager(client, systemctl):
    unit = settings_mod._user_unit()
    script = settings_mod._user_stop_script()
    unit.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    unit.write_text(CUSTOM_UNIT.format(project=PROJECT).replace("User=taos\n", ""))
    script.write_bytes(OLD_SCRIPT)

    resp = await _post_update(client, [])

    assert resp.status_code == 200, resp.text
    assert resp.json()["graceful_stop_refresh"]["scope"] == "user"
    assert script.read_bytes() == SHIPPED_SCRIPT
    assert "ExecStop=\n" in _dropin_of(unit).read_text()
    assert systemctl.calls == [("--user", "daemon-reload")]


@pytest.mark.asyncio
async def test_channel_switch_refreshes_the_same_way(client, system_install, systemctl, monkeypatch):
    unit, script = system_install
    original_unit = unit.read_text()
    restarts: list = []

    resp = await _post_channel(client, monkeypatch, restarts)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "switching" and restarts == [True]
    assert unit.read_text() == original_unit
    assert "ExecStop=\n" in _dropin_of(unit).read_text()
    assert script.read_bytes() == SHIPPED_SCRIPT
    assert systemctl.calls == [("daemon-reload",)]
    assert data["graceful_stop_refresh"]["problems"] == []


@pytest.mark.asyncio
async def test_channel_switch_reports_a_failed_refresh(client, system_install, systemctl, monkeypatch):
    systemctl.code, systemctl.err = 1, "denied"
    resp = await _post_channel(client, monkeypatch, [])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "Graceful-stop refresh:" in data["message"] and "denied" in data["message"]


# --------------------------------------------------------------------------
# the controller decides whether prepare-shutdown drains
# --------------------------------------------------------------------------

class FakeJobs:
    """systemctl list-jobs at the process boundary."""

    def __init__(self, out: bytes = b"", code: int = 0, missing: bool = False):
        self.out, self.code, self.missing = out, code, missing
        self.calls: list[tuple] = []

    async def __call__(self, *args, **kw):
        self.calls.append(args)
        if self.missing:
            raise FileNotFoundError("systemctl")
        proc = MagicMock()
        proc.returncode = self.code
        proc.communicate = AsyncMock(return_value=(self.out, b""))
        return proc


@pytest.fixture
def orchestrator(client):
    orch = MagicMock()
    orch.prepare = AsyncMock(return_value={"agents": "drained"})
    client._transport.app.state.orchestrator = orch
    yield orch
    client._transport.app.state.orchestrator = None


def _fake_jobs(monkeypatch, jobs: FakeJobs) -> None:
    monkeypatch.setattr("tinyagentos.routes.system.asyncio.create_subprocess_exec", jobs)


@pytest.mark.asyncio
async def test_restart_for_update_does_not_drain_without_a_shutdown_job(client, orchestrator, monkeypatch):
    from tinyagentos.routes.system import mark_restarting_for_update

    mark_restarting_for_update(client._transport.app.state)
    jobs = FakeJobs(b"80 networking.service start running\n")
    _fake_jobs(monkeypatch, jobs)

    resp = await client.post("/api/system/prepare-shutdown")

    assert resp.status_code == 200, resp.text
    assert resp.json()["drain_skipped"]
    orchestrator.prepare.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["shutdown", "reboot", "halt", "poweroff", "kexec"])
async def test_a_queued_shutdown_job_drains_even_during_an_update_restart(
    client, orchestrator, monkeypatch, target,
):
    from tinyagentos.routes.system import mark_restarting_for_update

    mark_restarting_for_update(client._transport.app.state)
    _fake_jobs(monkeypatch, FakeJobs(f"1 {target}.target start\n".encode()))

    resp = await client.post("/api/system/prepare-shutdown")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ready" and "drain_skipped" not in resp.json()
    orchestrator.prepare.assert_awaited_once_with("all", "system-shutdown")


@pytest.mark.asyncio
@pytest.mark.parametrize("jobs", [FakeJobs(code=1), FakeJobs(missing=True)], ids=["exit1", "no-systemctl"])
async def test_unknown_job_state_drains_rather_than_assuming_none(client, orchestrator, monkeypatch, jobs):
    from tinyagentos.routes.system import mark_restarting_for_update

    mark_restarting_for_update(client._transport.app.state)
    _fake_jobs(monkeypatch, jobs)

    resp = await client.post("/api/system/prepare-shutdown")

    assert resp.status_code == 200, resp.text
    orchestrator.prepare.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_ordinary_stop_drains_and_asks_systemd_nothing(client, orchestrator, monkeypatch):
    jobs = FakeJobs(b"80 networking.service start running\n")
    _fake_jobs(monkeypatch, jobs)

    resp = await client.post("/api/system/prepare-shutdown")

    assert resp.status_code == 200, resp.text
    orchestrator.prepare.assert_awaited_once_with("all", "system-shutdown")
    assert jobs.calls == []


@pytest.mark.asyncio
async def test_no_orchestrator_is_still_a_503(client):
    client._transport.app.state.orchestrator = None
    resp = await client.post("/api/system/prepare-shutdown")
    assert resp.status_code == 503, resp.text


@pytest.mark.asyncio
async def test_the_answer_is_asked_fresh_every_time(client, orchestrator, monkeypatch):
    """No cache: a shutdown job queued after an earlier 'no job' answer drains."""
    from tinyagentos.routes.system import mark_restarting_for_update

    mark_restarting_for_update(client._transport.app.state)
    _fake_jobs(monkeypatch, FakeJobs(b""))
    assert (await client.post("/api/system/prepare-shutdown")).json().get("drain_skipped")
    _fake_jobs(monkeypatch, FakeJobs(b"1 poweroff.target start\n"))
    assert "drain_skipped" not in (await client.post("/api/system/prepare-shutdown")).json()
    orchestrator.prepare.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_then_stop_skips_the_drain_end_to_end(
    client, system_install, systemctl, orchestrator, monkeypatch,
):
    """The real caller: the update route marks the restart, and the stop
    script's prepare-shutdown call that follows is answered without a drain."""
    resp = await _post_update(client, [])
    assert resp.status_code == 200, resp.text
    _fake_jobs(monkeypatch, FakeJobs(b""))

    stop = await client.post("/api/system/prepare-shutdown")

    assert stop.json().get("drain_skipped")
    orchestrator.prepare.assert_not_awaited()


# --------------------------------------------------------------------------
# the mark does not outlive a restart that did not happen
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_mark_is_cleared_when_the_restart_does_not_happen(monkeypatch):
    from types import SimpleNamespace

    from tinyagentos.routes import system

    state = SimpleNamespace()
    system.mark_restarting_for_update(state)

    async def restart_that_returns(app_state):  # every real success path exits the process
        return None

    monkeypatch.setattr(system, "_restart_controller", restart_that_returns)
    await system._do_restart(state)
    assert state._restarting_for_update is False

    system.mark_restarting_for_update(state)

    async def restart_that_raises(app_state):
        raise RuntimeError("systemctl exploded")

    monkeypatch.setattr(system, "_restart_controller", restart_that_raises)
    with pytest.raises(RuntimeError):
        await system._do_restart(state)
    assert state._restarting_for_update is False


@pytest.mark.asyncio
async def test_the_restart_route_marks_the_restart(client, monkeypatch):
    state = client._transport.app.state
    state._restarting_for_update = False

    async def fake_restart(_s):
        return None

    monkeypatch.setattr("tinyagentos.routes.system._do_restart", fake_restart)
    resp = await client.post("/api/system/restart/prepare")
    assert resp.status_code == 200, resp.text
    assert state._restarting_for_update is True


# --------------------------------------------------------------------------
# start-up
# --------------------------------------------------------------------------

def _startup_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "config.yaml").write_text(
        yaml.safe_dump({"server": {"host": "0.0.0.0", "port": 6969}})
    )
    return data_dir


@pytest.mark.asyncio
async def test_startup_refreshes_the_installed_files(tmp_path, system_install, systemctl):
    """The real lifespan runs the refresh (the first version called an async
    function through asyncio.to_thread, so it never ran)."""
    from tinyagentos.app import create_app

    unit, script = system_install
    app = create_app(data_dir=_startup_data_dir(tmp_path))
    async with app.router.lifespan_context(app):
        assert script.read_bytes() == SHIPPED_SCRIPT
        assert "ExecStop=\n" in _dropin_of(unit).read_text()
        assert app.state.graceful_stop_refresh["problems"] == []
    assert systemctl.calls == [("daemon-reload",)]


@pytest.mark.asyncio
async def test_startup_reports_a_failed_refresh(tmp_path, system_install, systemctl):
    from tinyagentos.app import create_app

    systemctl.code, systemctl.err = 1, "denied"
    app = create_app(data_dir=_startup_data_dir(tmp_path))
    async with app.router.lifespan_context(app):
        problems = app.state.graceful_stop_refresh["problems"]
        assert len(problems) == 1 and "denied" in problems[0]
