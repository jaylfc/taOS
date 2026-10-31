"""Dedicated controller listener for agent containers (tsk-vkxsec).

Agent requests reach the controller through an incus proxy device whose host
side connects from 127.0.0.1, so the controller sees them as loopback. They
get their own listener that rewrites the client address to a constant that is
never loopback.
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.deployer import DeployRequest, deploy_agent

MAIN_PORT = 6969
AGENT_PORT = 6972
DEVICE = "taos-proxy-taos"
PROJECT = "user-999"


# ---------------------------------------------------------------------------
# Deployer (RED-FIRST: written against the unchanged deployer)
# ---------------------------------------------------------------------------


async def _deploy_and_collect_devices(tmp_path, extra_config):
    req = DeployRequest(name="t", framework="smolagents", model=None, data_dir=tmp_path,
                        taos_port=MAIN_PORT, extra_config=extra_config)

    async def mock_exec(name, cmd, **kwargs):
        if "hostname -I" in " ".join(cmd):
            return (0, "10.0.0.5")
        return (0, "ok")

    with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock,
               return_value={"success": True, "name": "taos-agent-t"}), \
         patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
         patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
         patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock,
               return_value={"success": True, "output": ""}) as add_dev:
        result = await deploy_agent(req)
    return result, add_dev


def _device_kwargs(add_dev, name):
    for call in add_dev.await_args_list:
        if call.args[1] == name:
            return call.kwargs
    raise AssertionError(f"{name} was never attached")


@pytest.mark.asyncio
async def test_taos_proxy_device_connects_to_agent_port_never_main_port(tmp_path):
    result, add_dev = await _deploy_and_collect_devices(
        tmp_path, {"agent_controller_port": AGENT_PORT})
    assert result["success"] is True, result
    kw = _device_kwargs(add_dev, DEVICE)
    assert kw["connect"] != f"tcp:127.0.0.1:{MAIN_PORT}", "agent device still connects to the main port"
    assert kw["connect"] == f"tcp:127.0.0.1:{AGENT_PORT}"
    # In-container side is unchanged: agents see no difference.
    assert kw["listen"] == f"tcp:127.0.0.1:{MAIN_PORT}"


@pytest.mark.asyncio
async def test_deploy_is_refused_when_the_listener_is_not_live(tmp_path):
    result, add_dev = await _deploy_and_collect_devices(tmp_path, {"agent_controller_port": 0})
    assert result["success"] is False
    assert "agent controller listener is not verified" in result["error"]
    add_dev.assert_not_awaited()


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------


def _recording_app():
    seen: list[dict] = []

    async def main_app(scope, receive, send):
        seen.append(scope)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        else:
            await send({"type": "websocket.close", "code": 1000})

    return main_app, seen


async def _call(app, scope):
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _http_scope(client=("127.0.0.1", 5000)):
    return {"type": "http", "method": "GET", "path": "/", "raw_path": b"/", "query_string": b"",
            "headers": [], "client": client, "server": ("127.0.0.1", AGENT_PORT), "scheme": "http"}


@pytest.mark.asyncio
async def test_http_client_is_never_loopback_for_the_main_app():
    from tinyagentos import auth, auth_middleware
    from tinyagentos.agent_controller_listener import AGENT_CLIENT_HOST, create_agent_controller_app

    main_app, seen = _recording_app()
    app = create_agent_controller_app(main_app, identity="nonce")
    sent = await _call(app, _http_scope(("127.0.0.1", 5000)))

    host, _port = seen[0]["client"]
    assert host == AGENT_CLIENT_HOST
    ip = ipaddress.ip_address(host)
    assert not ip.is_loopback
    assert auth.is_console_origin(host, {}) is False
    request = MagicMock()
    request.client.host = host
    assert auth_middleware._is_loopback_client(request) is False
    # The identity header is stamped on the response.
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert (b"x-taos-agent-listener", b"nonce") in start["headers"]


@pytest.mark.asyncio
async def test_websocket_scope_client_is_rewritten_too():
    from tinyagentos.agent_controller_listener import AGENT_CLIENT_HOST, create_agent_controller_app

    main_app, seen = _recording_app()
    app = create_agent_controller_app(main_app, identity="nonce")
    scope = {"type": "websocket", "path": "/ws", "raw_path": b"/ws", "query_string": b"",
             "headers": [], "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", AGENT_PORT),
             "scheme": "ws", "subprotocols": []}
    await _call(app, scope)
    assert seen[0]["type"] == "websocket"
    assert seen[0]["client"] == (AGENT_CLIENT_HOST, 0)


@pytest.mark.asyncio
async def test_lifespan_is_answered_by_the_wrapper():
    from tinyagentos.agent_controller_listener import create_agent_controller_app

    main_app, seen = _recording_app()
    app = create_agent_controller_app(main_app, identity="nonce")
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list[dict] = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)
    assert [m["type"] for m in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert seen == []


# ---------------------------------------------------------------------------
# /auth/swipe-unlock: refused through the wrapper, still works from the console
# ---------------------------------------------------------------------------

PASSWORD = "correct horse battery staple"
UA = "Mozilla/5.0 (Linux; taOS kiosk) Chromium/131"


@pytest_asyncio.fixture()
async def swipe_app(tmp_path, monkeypatch):
    from tinyagentos.app import create_app
    from tinyagentos.auth import AuthManager
    from tinyagentos.routes import auth as auth_routes

    monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    application = create_app(data_dir=tmp_path)
    mgr = AuthManager(tmp_path)
    mgr.setup_user("owner", "Device Owner", "", PASSWORD)
    data = mgr._read_users()
    for u in data["users"]:
        if u["username"] == "owner":
            u["unlock_method"] = "swipe"
    mgr._write_users(data)
    application.state.auth = mgr
    yield application
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()


def _post_client(asgi_app):
    return AsyncClient(
        transport=ASGITransport(app=asgi_app, client=("127.0.0.1", 51234)),
        base_url="http://localhost:6969",
        headers={"User-Agent": UA, "X-taOS-Console": "1"},
    )


@pytest.mark.asyncio
async def test_swipe_unlock_is_refused_through_the_agent_listener(swipe_app):
    from tinyagentos.agent_controller_listener import create_agent_controller_app

    # Positive control: straight to the main app from the device's own console.
    async with _post_client(swipe_app) as console:
        ok = await console.post("/auth/swipe-unlock")
    assert ok.status_code == 200, ok.text
    assert "taos_session" in ok.cookies

    # Same request, same claimed peer (127.0.0.1), through the agent listener.
    wrapped = create_agent_controller_app(swipe_app, identity="nonce")
    async with _post_client(wrapped) as agent:
        r = await agent.post("/auth/swipe-unlock")
    assert r.status_code == 403
    assert "taos_session" not in r.cookies


# ---------------------------------------------------------------------------
# Startup reconcile of existing agents
# ---------------------------------------------------------------------------


class FakeIncus:
    def __init__(self, projects, connect):
        self.projects = dict(projects)
        self.connect = dict(connect)
        self.calls: list[list[str]] = []

    async def run(self, cmd, timeout=120):
        self.calls.append(list(cmd))
        project = cmd[cmd.index("--project") + 1] if "--project" in cmd else None
        args = [a for i, a in enumerate(cmd) if a != "--project" and (i == 0 or cmd[i - 1] != "--project")]
        if args[:2] == ["incus", "list"]:
            return 0, json.dumps([{"name": n, "project": p} for n, p in self.projects.items()])
        name = args[4]
        if project != self.projects.get(name):
            return 1, "not found"
        if args[3] == "get":
            return 0, self.connect[name] + "\n"
        assert args[3] == "set" and args[5] == DEVICE
        self.connect[name] = args[6].partition("=")[2]
        return 0, ""

    def sets(self):
        return [c for c in self.calls if c[3:4] == ["set"]]


@pytest.mark.asyncio
async def test_reconcile_moves_main_port_device_and_leaves_agent_port_alone():
    from tinyagentos.agent_controller_listener import reconcile_agents

    main, agent = f"tcp:127.0.0.1:{MAIN_PORT}", f"tcp:127.0.0.1:{AGENT_PORT}"
    fake = FakeIncus(
        {"taos-agent-legacy": "default", "taos-agent-old": PROJECT, "taos-agent-done": PROJECT},
        {"taos-agent-legacy": main, "taos-agent-old": main, "taos-agent-done": agent},
    )
    agents = [{"name": "legacy"}, {"name": "old"}, {"name": "done"}]
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await reconcile_agents(agents=agents, main_port=MAIN_PORT, agent_port=AGENT_PORT,
                                        listener_ready=True)
    assert fake.connect == {"taos-agent-legacy": agent, "taos-agent-old": agent, "taos-agent-done": agent}
    assert sorted(i["agent"] for i in report["repointed"]) == ["legacy", "old"]
    assert [i["agent"] for i in report["unchanged"]] == ["done"]
    # every set names the container's own project, default included
    assert {c[c.index("--project") + 1] for c in fake.sets()} == {"default", PROJECT}
    assert not any(c[4] == "taos-agent-done" for c in fake.sets())


@pytest.mark.asyncio
async def test_reconcile_moves_nothing_when_the_listener_is_not_verified():
    from tinyagentos.agent_controller_listener import reconcile_agents

    main = f"tcp:127.0.0.1:{MAIN_PORT}"
    fake = FakeIncus({"taos-agent-old": PROJECT}, {"taos-agent-old": main})
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await reconcile_agents(agents=[{"name": "old"}], main_port=MAIN_PORT,
                                        agent_port=AGENT_PORT, listener_ready=False)
    assert fake.connect["taos-agent-old"] == main
    assert fake.sets() == [] and len(report["skipped"]) == 1


@pytest.mark.asyncio
async def test_liveness_probe_requires_this_starts_nonce(monkeypatch):
    import httpx

    from tinyagentos import agent_controller_listener as acl

    def handler_for(header_value):
        def handler(request):
            assert request.url.path == acl.PROBE_PATH
            return httpx.Response(503, headers={"x-taos-agent-listener": header_value} if header_value else {})
        return handler

    real = httpx.AsyncClient
    for answer, expected in (("mine", True), ("other", False), (None, False)):
        monkeypatch.setattr(httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(handler_for(answer)), **kw))
        assert await acl.wait_for_listener(AGENT_PORT, identity="mine", attempts=1, delay=0) is expected
    assert await acl.wait_for_listener(0, identity="mine") is False
    assert await acl.wait_for_listener(AGENT_PORT, identity=None) is False
