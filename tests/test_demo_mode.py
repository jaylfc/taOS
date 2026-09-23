"""Settings -> Demo mode: one runtime switch over every TAOS_LOCK_DEMO_* flag.

The env flags say WHAT demo content exists; the switch says whether it is
SHOWN. With the switch off, every surface must take the path it takes with its
flag unset -- 404s included -- so each surface is measured below with the
flags ON and the switch OFF, then the switch back ON as the positive control.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import AuthManager
from tinyagentos.demo_mode import read_demo_mode, write_demo_mode

UA = "Mozilla/5.0 (demo test)"
CSRF = "c" * 64
DEMO_ENV = {
    "TAOS_LOCK_DEMO_AGENTS": "Ann:hermes:Drafting,Bob:openclaw:Filing",
    "TAOS_LOCK_DEMO_NOTIFICATIONS": "1",
    "TAOS_LOCK_DEMO_PANELS": "1",
    "TAOS_LOCK_DEMO_DECISION": "Ship the demo?",
    "TAOS_LOCK_DEMO_DECISION_AGENT": "Bob",
}


@pytest.fixture()
def demo_env(monkeypatch):
    for k, v in DEMO_ENV.items():
        monkeypatch.setenv(k, v)


def _make_app(data_dir: Path, monkeypatch):
    from tinyagentos.app import create_app

    monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
    app = create_app(data_dir=data_dir)
    mgr = AuthManager(data_dir)
    if not mgr.find_user("admin"):
        mgr.setup_user("admin", "Admin", "", "admin password 123")
    app.state.auth = mgr
    return app


@pytest.fixture()
def app(tmp_path, monkeypatch, demo_env):
    return _make_app(tmp_path, monkeypatch)


def _client(app, host="127.0.0.1"):
    return AsyncClient(
        transport=ASGITransport(app=app, client=(host, 51234)),
        base_url="http://localhost:6969",
        headers={"User-Agent": UA},
    )


def _sign_in(client, app, username):
    mgr = app.state.auth
    token = mgr.create_session(user_id=mgr.find_user(username)["id"], user_agent=UA)
    client.cookies.set("taos_session", token)
    client.cookies.set("csrf_token", CSRF)


@pytest_asyncio.fixture()
async def console(app):
    async with _client(app) as c:
        yield c


async def _surfaces(client) -> dict:
    """One observation per kind of demo content."""
    widgets = (await client.get("/auth/lock-widgets")).json()
    names = {a["name"] for a in widgets.get("agents", [])}
    decisions = [a for a in widgets.get("agents", []) if (a.get("decision") or {}).get("demo")]
    stats = (await client.get("/auth/lock-stats")).json()
    return {
        "agents": bool({"Ann", "Bob"} & names),                      # TAOS_LOCK_DEMO_AGENTS
        "threads": bool(widgets.get("threads")),
        "decision": bool(decisions),                                 # ..._DECISION(_AGENT)
        "thread_route": (await client.get("/auth/lock-thread/ann")).status_code,
        "stats_agents": "agents" in stats,
        "notifications": (await client.get("/auth/lock-notifications")).status_code,
        "panels": (await client.get("/auth/lock-panels")).status_code,  # ..._PANELS
    }


ALL_ON = {
    "agents": True, "threads": True, "decision": True, "thread_route": 200,
    "stats_agents": True, "notifications": 200, "panels": 200,
}
ALL_OFF = {
    "agents": False, "threads": False, "decision": False, "thread_route": 404,
    "stats_agents": False, "notifications": 404, "panels": 404,
}


class TestTheSwitchGatesEverySurface:

    @pytest.mark.asyncio
    async def test_default_is_on_when_flags_are_set(self, app, console):
        assert await _surfaces(console) == ALL_ON

    @pytest.mark.asyncio
    async def test_off_hides_every_kind_and_on_restores_them(self, app, console):
        _sign_in(console, app, "admin")
        r = await console.put("/api/settings/demo-mode", json={"enabled": False},
                              headers={"X-CSRF-Token": CSRF})
        assert r.status_code == 200 and r.json()["enabled"] is False
        console.cookies.clear()
        assert await _surfaces(console) == ALL_OFF

        _sign_in(console, app, "admin")
        r = await console.put("/api/settings/demo-mode", json={"enabled": True},
                              headers={"X-CSRF-Token": CSRF})
        assert r.status_code == 200
        console.cookies.clear()
        assert await _surfaces(console) == ALL_ON

    @pytest.mark.asyncio
    async def test_off_is_the_same_as_the_flags_unset(self, app, console, monkeypatch):
        """The switch-off answers must be exactly the flag-unset answers."""
        write_demo_mode(app.state.data_dir, False)
        switched_off = await _surfaces(console)
        write_demo_mode(app.state.data_dir, True)
        for k in DEMO_ENV:
            monkeypatch.delenv(k)
        assert await _surfaces(console) == switched_off

    @pytest.mark.asyncio
    async def test_it_persists_across_a_restart(self, tmp_path, monkeypatch, demo_env):
        first = _make_app(tmp_path, monkeypatch)
        async with _client(first) as c:
            _sign_in(c, first, "admin")
            r = await c.put("/api/settings/demo-mode", json={"enabled": False},
                            headers={"X-CSRF-Token": CSRF})
            assert r.status_code == 200
        second = _make_app(tmp_path, monkeypatch)
        async with _client(second) as c:
            assert await _surfaces(c) == ALL_OFF
            _sign_in(c, second, "admin")
            body = (await c.get("/api/settings/demo-mode")).json()
            assert body == {"enabled": False, "available": True}


class TestWhoMayFlipIt:

    @pytest.mark.asyncio
    async def test_non_admin_put_is_403(self, app, console):
        mgr = app.state.auth
        code = mgr.add_user_invite("guest", "admin")
        mgr.complete_invite("guest", code, "Guest", "", "guest password 1")
        _sign_in(console, app, "guest")
        r = await console.put("/api/settings/demo-mode", json={"enabled": False},
                              headers={"X-CSRF-Token": CSRF})
        assert r.status_code == 403
        assert read_demo_mode(app.state.data_dir) is True
        assert (await console.get("/api/settings/demo-mode")).status_code == 403

    @pytest.mark.asyncio
    async def test_signed_out_cannot_flip_it(self, app, console):
        r = await console.put("/api/settings/demo-mode", json={"enabled": False})
        assert r.status_code in (401, 403)
        assert read_demo_mode(app.state.data_dir) is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [{}, {"enabled": "false"}, {"enabled": 0}, [True]])
    async def test_a_non_boolean_is_400(self, app, console, body):
        _sign_in(console, app, "admin")
        r = await console.put("/api/settings/demo-mode", json=body,
                              headers={"X-CSRF-Token": CSRF})
        assert r.status_code == 400
        assert read_demo_mode(app.state.data_dir) is True


class TestTheStore:

    def test_no_file_follows_the_flags(self, tmp_path, monkeypatch):
        for k in DEMO_ENV:
            monkeypatch.delenv(k, raising=False)
        assert read_demo_mode(tmp_path) is False
        monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "Ann")
        assert read_demo_mode(tmp_path) is True

    @pytest.mark.parametrize("raw", ["{", "[]", '{"enabled": "yes"}', "{}"])
    def test_a_damaged_file_reads_as_off(self, tmp_path, demo_env, raw):
        (tmp_path / "demo_mode.json").write_text(raw)
        assert read_demo_mode(tmp_path) is False

    def test_no_data_dir_reads_as_off(self, demo_env):
        assert read_demo_mode(None) is False


def test_every_demo_flag_read_goes_through_the_helper():
    """A TAOS_LOCK_DEMO_* read that bypassed demo_env would ignore the switch."""
    root = Path(__file__).resolve().parents[1] / "tinyagentos"
    offenders = []
    # Spans newlines: the original decision read was split over three lines.
    pattern = re.compile(
        r"(?:environ(?:\.get)?|getenv)\s*[(\[]\s*[\"']TAOS_LOCK_DEMO"
    )
    for path in root.rglob("*.py"):
        if path.name == "demo_mode.py":
            continue
        for m in pattern.finditer(path.read_text()):
            offenders.append(f"{path.relative_to(root)}: {m.group(0)!r}")
    assert not offenders, offenders
