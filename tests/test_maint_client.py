"""Controller side of the maintenance runner: trigger, wait, report, notify."""
from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tinyagentos import maint, qmd_service


@pytest.fixture
def runner_dirs(tmp_path, monkeypatch):
    state = tmp_path / "var"
    (state / "trigger").mkdir(parents=True)
    helper = tmp_path / "taos-maint"
    helper.write_text("#!/bin/sh\n")
    conf = tmp_path / "maint.conf"
    conf.write_text("TREE=/x\n")
    monkeypatch.setattr(maint, "STATE_DIR", state)
    monkeypatch.setattr(maint, "HELPER", helper)
    monkeypatch.setattr(maint, "CONF", conf)
    monkeypatch.setattr(maint, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(maint.platform, "system", lambda: "Linux")
    monkeypatch.setattr(maint, "_in_container", lambda: False)
    monkeypatch.setattr(maint.os, "geteuid", lambda: 1000)
    real_exists = maint.Path.exists
    monkeypatch.setattr(maint.Path, "exists",
                        lambda self: True if str(self) == "/run/systemd/system" else real_exists(self))
    return state


def test_availability_states(runner_dirs, monkeypatch):
    assert maint.availability()["mode"] == "ready"
    maint.HELPER.unlink()
    a = maint.availability()
    assert a["mode"] == "setup-needed" and "not installed" in a["reason"]
    assert "install-maint.sh" in maint.summary()["setup_command"] and "sudo bash" in maint.summary()["setup_command"]
    monkeypatch.setattr(maint, "_in_container", lambda: True)
    assert maint.availability()["mode"] == "unsupported"
    monkeypatch.setattr(maint, "_in_container", lambda: False)
    monkeypatch.setattr(maint.platform, "system", lambda: "Darwin")
    assert maint.availability()["mode"] == "unsupported"


async def _fake_runner(state_dir, result="applied", steps=None, delay=0.05):
    """Plays the root runner: waits for a request file, deletes it, writes status."""
    for _ in range(400):
        reqs = list((state_dir / "trigger").glob("*.req"))
        if reqs:
            for r in reqs:
                r.unlink()
            await asyncio.sleep(delay)
            prev = json.loads((state_dir / "status.json").read_text()) if (state_dir / "status.json").exists() else {}
            (state_dir / "status.json").write_text(json.dumps(
                {"run_seq": prev.get("run_seq", 0) + 1, "state": "idle", "result": result, "steps": steps or []}))
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_run_pending_triggers_and_waits_for_the_runner(runner_dirs):
    (runner_dirs / "status.json").write_text(json.dumps({"run_seq": 4, "state": "idle", "result": "nothing"}))
    task = asyncio.create_task(_fake_runner(runner_dirs, "applied", [{"id": "a", "result": "applied"}]))
    report = await maint.run_pending(wait=5)
    await task
    assert report["result"] == "applied" and report["steps"][0]["id"] == "a"
    assert list((runner_dirs / "trigger").iterdir()) == []


@pytest.mark.asyncio
async def test_run_pending_ignores_an_older_status_and_times_out(runner_dirs):
    (runner_dirs / "status.json").write_text(json.dumps({"run_seq": 4, "state": "idle", "result": "applied"}))
    report = await maint.run_pending(wait=0.2)
    assert report["result"] == "timeout"


@pytest.mark.asyncio
async def test_run_pending_without_a_runner_does_not_try(runner_dirs):
    maint.HELPER.unlink()
    report = await maint.run_pending(wait=0.2)
    assert report["result"] == "unavailable" and list((runner_dirs / "trigger").iterdir()) == []


def test_failure_text():
    assert maint.failure_text({"mode": "ready", "result": "applied"}) is None
    assert maint.failure_text({"mode": "ready", "result": "nothing"}) is None
    assert maint.failure_text({"mode": "unsupported", "result": "unavailable"}) is None
    assert "install-maint.sh" in maint.failure_text({"mode": "setup-needed", "result": "unavailable"})
    text = maint.failure_text({"mode": "ready", "result": "failed",
                               "steps": [{"id": "qmd-upgrade-2.8.3", "result": "failed", "detail": "npm exploded"}]})
    assert "qmd-upgrade-2.8.3" in text and "npm exploded" in text


# ---------------------------------------------------------- qmd startup check

class _Notes:
    def __init__(self):
        self.items = []

    async def add(self, **kw):
        self.items.append(kw)


def _state(handler):
    return SimpleNamespace(
        qmd_client=SimpleNamespace(base_url="http://qmd.test"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        notifications=_Notes(),
        auth=SimpleNamespace(get_primary_user=lambda: {"id": "owner"}),
    )


@pytest.fixture
def fast(monkeypatch):
    real = asyncio.sleep

    async def quick(_s):
        await real(0)
    monkeypatch.setattr(asyncio, "sleep", quick)


def _flip_handler(flag):
    def handler(request):
        if request.url.path == "/delete-chunk":
            return httpx.Response(400, json={"error": "bad hash"}) if flag["new"] else httpx.Response(404, json={})
        return httpx.Response(200, json={})
    return handler


@pytest.mark.asyncio
async def test_startup_applies_the_step_and_stays_quiet_when_it_works(fast, monkeypatch):
    flag = {"new": False}
    state = _state(_flip_handler(flag))

    async def fake_run(**kw):
        flag["new"] = True
        return {"mode": "ready", "result": "applied", "steps": [{"id": "x", "result": "applied"}]}
    monkeypatch.setattr(maint, "availability", lambda: {"mode": "ready", "reason": ""})
    monkeypatch.setattr(maint, "run_pending", fake_run)
    monkeypatch.setattr(qmd_service, "qmd_bind_exposed", lambda *a, **k: False)
    await qmd_service.run_qmd_service_check(state)
    assert state.notifications.items == []


@pytest.mark.asyncio
async def test_startup_failure_notifies_with_the_step_error(fast, monkeypatch):
    state = _state(_flip_handler({"new": False}))

    async def fake_run(**kw):
        return {"mode": "ready", "result": "failed",
                "steps": [{"id": "qmd-upgrade-2.8.3", "result": "failed", "detail": "npm exploded"}]}
    monkeypatch.setattr(maint, "availability", lambda: {"mode": "ready", "reason": ""})
    monkeypatch.setattr(maint, "run_pending", fake_run)
    monkeypatch.setattr(qmd_service, "qmd_bind_exposed", lambda *a, **k: False)
    await qmd_service.run_qmd_service_check(state)
    (note,) = state.notifications.items
    assert "npm exploded" in note["message"] and note["user_id"] == "owner"


@pytest.mark.asyncio
async def test_startup_without_a_runner_shows_the_one_time_setup(fast, monkeypatch):
    state = _state(_flip_handler({"new": False}))
    monkeypatch.setattr(maint, "availability", lambda: {"mode": "setup-needed", "reason": "x"})
    monkeypatch.setattr(qmd_service, "qmd_bind_exposed", lambda *a, **k: False)
    await qmd_service.run_qmd_service_check(state)
    (note,) = state.notifications.items
    assert "one manual step" in note["message"] and "install-maint.sh" in note["message"]


@pytest.mark.asyncio
async def test_startup_unsupported_platform_keeps_the_manual_instruction(fast, monkeypatch):
    state = _state(_flip_handler({"new": False}))
    monkeypatch.setattr(maint, "availability", lambda: {"mode": "unsupported", "reason": "mac"})
    monkeypatch.setattr(qmd_service, "qmd_bind_exposed", lambda *a, **k: False)
    await qmd_service.run_qmd_service_check(state)
    (note,) = state.notifications.items
    assert "upgrade-qmd.sh" in note["message"]


@pytest.mark.asyncio
async def test_a_current_qmd_with_nothing_pending_is_silent(fast, monkeypatch):
    state = _state(_flip_handler({"new": True}))

    async def fake_run(**kw):
        return {"mode": "ready", "result": "nothing", "steps": []}
    monkeypatch.setattr(maint, "availability", lambda: {"mode": "ready", "reason": ""})
    monkeypatch.setattr(maint, "run_pending", fake_run)
    monkeypatch.setattr(qmd_service, "qmd_bind_exposed", lambda *a, **k: False)
    await qmd_service.run_qmd_service_check(state)
    assert state.notifications.items == []


# --------------------------------------------------------------- update route

@pytest.mark.asyncio
async def test_update_completes_and_notifies_when_a_maintenance_step_fails(client, app):
    from test_settings_taosmd_update import _update_route_patches

    bad = {"mode": "ready", "result": "failed",
           "steps": [{"id": "qmd-upgrade-2.8.3", "result": "failed", "detail": "npm exploded"}]}
    added = []
    real_add = app.state.notifications.add

    async def spy(**kw):
        added.append(kw)
        return await real_add(**kw)
    with contextlib.ExitStack() as stack:
        for p in _update_route_patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(maint, "run_pending", AsyncMock(return_value=bad)))
        stack.enter_context(patch.object(app.state.notifications, "add", spy))
        resp = await client.post("/api/settings/update")
    assert resp.status_code == 200 and resp.json()["status"] == "restarting"
    assert resp.json()["maintenance"]["result"] == "failed"
    assert any("npm exploded" in n["message"] for n in added)


@pytest.mark.asyncio
async def test_update_is_quiet_when_nothing_is_pending(client, app):
    from test_settings_taosmd_update import _update_route_patches

    added = []
    real_add = app.state.notifications.add

    async def spy(**kw):
        added.append(kw)
        return await real_add(**kw)
    with contextlib.ExitStack() as stack:
        for p in _update_route_patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(maint, "run_pending", AsyncMock(return_value={"mode": "ready", "result": "nothing", "steps": []})))
        stack.enter_context(patch.object(app.state.notifications, "add", spy))
        resp = await client.post("/api/settings/update")
    assert resp.status_code == 200 and not [n for n in added if n.get("source") == "system.maintenance"]


@pytest.mark.asyncio
async def test_settings_maintenance_endpoint_reports_setup_needed(client, monkeypatch):
    monkeypatch.setattr(maint, "availability", lambda: {"mode": "setup-needed", "reason": "no runner"})
    resp = await client.get("/api/settings/maintenance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "setup-needed" and "install-maint.sh" in body["setup_command"]
