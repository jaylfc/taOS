"""The system taOS Agent on PicoClaw (tsk-x2joom): real apps, fake binaries.

Two real apps (``create_app`` on module tmp data dirs, ``TAOS_LLM_GATEWAY=1``):

- a DESKTOP host, where ``systemctl cat taos-kiosk.service`` fails, and
- a MOBILE host, where it succeeds (a fake ``systemctl`` on PATH, so the
  real ``hardware._detect_device_class`` runs and decides).

The mobile app is also served by a real uvicorn on an ephemeral loopback
port, because PicoClaw is a subprocess that calls the gateway over HTTP. The
``picoclaw`` on PATH is a small fake that does what the real 0.3.1 binary was
measured doing: read ``$PICOCLAW_CONFIG``, POST one non-streaming chat
completion to ``<api_base>/chat/completions`` with the model's key, print a
banner and the answer after a lobster. Only the gateway's UPSTREAM is mocked
(respx), so auth, the key's allowlist, taos-default resolution and forwarding
all run for real.

Hostile cases first; the happy path last.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import respx
import yaml
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.app import create_app

UPSTREAM = "http://llm.test:8080/v1"
UPSTREAM_CHAT = f"{UPSTREAM}/chat/completions"
UPSTREAM_KEY = "sk-upstream-picoclaw-test"
BACKEND = {
    "name": "local-llama",
    "type": "openai-compatible",
    "url": UPSTREAM,
    "models": [{"id": "qwen3-8b"}, {"id": "gpt-small"}],
    "api_key": UPSTREAM_KEY,
    "priority": 1,
}
GATEWAY = "/api/llm/v1"
FALLBACK_REASON = "picoclaw preferred, gateway disabled, using opencode"
_ASYNC = pytest.mark.asyncio(loop_scope="module")

# ---------------------------------------------------------------------------
# Fake binaries
# ---------------------------------------------------------------------------

_FAKE_SYSTEMCTL = """#!/bin/sh
# taos-kiosk.service exists only on a taOSmobile handset.
if [ "$1" = "cat" ] && [ "$2" = "taos-kiosk.service" ]; then exit {kiosk_rc}; fi
exit 1
"""

_FAKE_PICOCLAW = r'''#!{python}
"""Stand-in for picoclaw 0.3.1 `agent -m <text> -s <session>`."""
import json, os, sys, urllib.request, urllib.error

args = [a for a in sys.argv[1:] if a != "--no-color"]
cfg_path = os.environ.get("PICOCLAW_CONFIG") or os.path.expanduser("~/.picoclaw/config.json")
with open(os.path.join(os.path.dirname(cfg_path), "fake-invocations.jsonl"), "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(),
                         "config": cfg_path, "env": sorted(os.environ)}}) + "\n")
if args[:1] != ["agent"] or "-m" not in args:
    print("unsupported invocation", file=sys.stderr); sys.exit(2)
text = args[args.index("-m") + 1]
cfg = json.load(open(cfg_path))
d = cfg["agents"]["defaults"]
entry = next(m for m in cfg["model_list"] if m["model_name"] == d["model_name"])
model = entry["model"].split("/", 1)[1] if "/" in entry["model"] else entry["model"]
messages = []
agents_md = os.path.join(d["workspace"], "AGENTS.md")
if os.path.exists(agents_md):
    messages.append({{"role": "system", "content": open(agents_md).read()}})
messages.append({{"role": "user", "content": text}})
req = urllib.request.Request(
    entry["api_base"].rstrip("/") + "/chat/completions",
    data=json.dumps({{"model": model, "messages": messages}}).encode(),
    headers={{"Authorization": "Bearer " + entry["api_keys"][0],
              "Content-Type": "application/json"}},
)
try:
    body = json.load(urllib.request.urlopen(req, timeout=20))
except urllib.error.HTTPError as e:
    print("Error: LLM call failed: status %d" % e.code, file=sys.stderr); sys.exit(1)
print("\x1b[1;38;2;62;93;185mPICOCLAW BANNER\x1b[0m\r\n")
print("\U0001F99E " + body["choices"][0]["message"]["content"])
'''


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _write_test_config(data_dir: Path) -> None:
    config = {
        "server": {"host": "127.0.0.1", "port": 6969},
        "backends": [dict(BACKEND)],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (data_dir / "config.yaml").write_text(yaml.dump(config))
    (data_dir / ".setup_complete").touch()


async def _build_app(tmp_path_factory, name: str, *, mobile: bool):
    data_dir = tmp_path_factory.mktemp(name)
    _write_test_config(data_dir)
    bindir = tmp_path_factory.mktemp(name + "-bin")
    _write_exec(bindir / "systemctl", _FAKE_SYSTEMCTL.format(kiosk_rc=0 if mobile else 1))
    _write_exec(bindir / "picoclaw", _FAKE_PICOCLAW.format(python=sys.executable))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        mp.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
        app = create_app(data_dir=data_dir)
    state = app.state
    for store in (state.desktop_settings, state.secrets):
        if store._db is not None:
            await store.close()
        await store.init()
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    )
    return app, client, bindir


async def _close_app(app, client) -> None:
    await client.aclose()
    for store in (app.state.desktop_settings, app.state.secrets):
        await store.close()
    await app.state.http_client.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def desktop_env(tmp_path_factory):
    app, client, bindir = await _build_app(tmp_path_factory, "desktop", mobile=False)
    yield app, client, bindir
    await _close_app(app, client)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def mobile_env(tmp_path_factory):
    """The mobile app, also listening on a real loopback port for PicoClaw."""
    import uvicorn

    app, client, bindir = await _build_app(tmp_path_factory, "mobile", mobile=True)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=0, lifespan="off", log_level="warning",
    ))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    app.state.config.server["port"] = port
    yield app, client, bindir
    server.should_exit = True
    await task
    await _close_app(app, client)


async def _reset(app, client, bindir, monkeypatch) -> None:
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    resp = await client.put("/api/taos-agent/framework",
                            json={"framework": "auto", "device_class": "auto"})
    assert resp.status_code == 200, resp.text
    await app.state.desktop_settings.save_preference(
        "user", "taos_agent", {"model": "qwen3-8b", "permitted_models": ["qwen3-8b"]})
    app.state.config.backends = [dict(BACKEND)]
    respx.mock.clear()
    respx.mock.reset()


@pytest_asyncio.fixture(loop_scope="module")
async def desktop(desktop_env, monkeypatch):
    app, client, bindir = desktop_env
    await _reset(app, client, bindir, monkeypatch)
    yield app, client
    await _reset(app, client, bindir, monkeypatch)


@pytest_asyncio.fixture(loop_scope="module")
async def mobile(mobile_env, monkeypatch):
    app, client, bindir = mobile_env
    await _reset(app, client, bindir, monkeypatch)
    yield app, client
    await _reset(app, client, bindir, monkeypatch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _picoclaw_config_path(app) -> Path:
    return Path(app.state.data_dir) / "taos-agent-picoclaw" / "config.json"


def _key_from_config(app) -> str:
    cfg = json.loads(_picoclaw_config_path(app).read_text())
    keys = {k for m in cfg["model_list"] for k in m["api_keys"]}
    assert len(keys) == 1, keys
    return keys.pop()


def _live_keys(app) -> int:
    """How many gateway keys are live for the taOS Agent (probe, then restore
    nothing: callers only use it where zero is the expected answer)."""
    from tinyagentos.llm_gateway.auth import revoke_keys_for
    return revoke_keys_for("agent:taos-agent", data_dir=app.state.data_dir)


async def _framework(client) -> dict:
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _lock_framework(client, monkeypatch) -> str:
    from tinyagentos.routes import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)
    resp = await client.get("/auth/lock-widgets")
    assert resp.status_code == 200, resp.text
    agent = next(a for a in resp.json()["agents"] if a.get("system"))
    return agent["framework"]


async def _models_with(app, key: str) -> httpx.Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {key}"}) as c:
        return await c.get(GATEWAY + "/models")


def _completion(content: str, model: str = "qwen3-8b") -> dict:
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


async def _chat(client, text: str = "hello") -> tuple[str, list[dict]]:
    resp = await client.post("/api/taos-agent/chat",
                             json={"messages": [{"role": "user", "content": text}]})
    assert resp.status_code == 200, resp.text
    frames = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    return resp.text, frames


# ---------------------------------------------------------------------------
# Hostile: hosts that are not mobile see no change
# ---------------------------------------------------------------------------

@_ASYNC
async def test_desktop_host_auto_is_opencode_and_mints_no_key(desktop, monkeypatch):
    app, client = desktop
    cfg = await _framework(client)
    assert cfg["framework"] == "opencode"
    assert cfg["framework_preference"] == "opencode"
    assert cfg["device_class"] != "mobile"
    assert await _lock_framework(client, monkeypatch) == "opencode"
    assert not _picoclaw_config_path(app).exists()
    assert _live_keys(app) == 0


@_ASYNC
async def test_desktop_host_chat_still_goes_to_opencode(desktop, monkeypatch):
    """The non-mobile chat path is untouched: it still asks for the opencode
    server (and never spawns picoclaw)."""
    app, client = desktop
    from tinyagentos.routes import taos_agent as ta

    called = {}

    async def fake_ensure(state, model):
        called["model"] = model
        raise RuntimeError("opencode stand-in")

    class _Proxy:
        port = 7834

        def is_running(self):
            return True

    monkeypatch.setattr(ta, "ensure_taos_opencode_server", fake_ensure)
    monkeypatch.setattr(app.state, "llm_proxy", _Proxy(), raising=False)
    resp = await client.post("/api/taos-agent/chat",
                             json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert "opencode stand-in" in resp.text
    assert called == {"model": "qwen3-8b"}
    assert not _picoclaw_config_path(app).exists()


# ---------------------------------------------------------------------------
# Hostile: the operator override and bad values
# ---------------------------------------------------------------------------

@_ASYNC
async def test_mobile_with_override_opencode_is_opencode(mobile, monkeypatch):
    app, client = mobile
    resp = await client.put("/api/taos-agent/framework", json={"framework": "opencode"})
    assert resp.status_code == 200, resp.text
    cfg = await _framework(client)
    assert cfg["device_class"] == "mobile"
    assert cfg["framework"] == "opencode"
    assert cfg["framework_preference"] == "opencode"
    assert await _lock_framework(client, monkeypatch) == "opencode"
    assert not _picoclaw_config_path(app).exists()
    assert _live_keys(app) == 0


@_ASYNC
@pytest.mark.parametrize("body", [
    {"framework": "hermes"},
    {"framework": ""},
    {"framework": 7},
    {"device_class": "toaster"},
])
async def test_unknown_values_are_rejected_and_change_nothing(mobile, body):
    app, client = mobile
    before = await _framework(client)
    on_disk_before = yaml.safe_load(Path(app.state.config.config_path).read_text())
    resp = await client.put("/api/taos-agent/framework", json=body)
    assert resp.status_code in (400, 422), resp.text
    assert await _framework(client) == before
    assert yaml.safe_load(Path(app.state.config.config_path).read_text()) == on_disk_before


@_ASYNC
async def test_unknown_value_in_config_file_is_treated_as_auto(mobile, caplog):
    """A hand-edited config.yaml with a bad value must not crash startup or
    pick a harness by accident: it is ignored (auto) with a warning."""
    app, client = mobile
    from tinyagentos.taos_agent_runtime import refresh_framework_decision

    app.state.config.taos_agent = {"framework": "PicoClaw!!"}
    caplog.set_level(logging.WARNING, logger="tinyagentos.taos_agent_runtime")
    decision = refresh_framework_decision(app.state)
    assert decision.preference == "picoclaw"  # auto on a mobile host
    assert "PicoClaw!!" in caplog.text


@_ASYNC
async def test_framework_switch_requires_admin(mobile):
    app, client = mobile
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           event_hooks=csrf_event_hooks()) as anon:
        resp = await anon.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    assert resp.status_code in (401, 403)
    assert not _picoclaw_config_path(app).exists()


# ---------------------------------------------------------------------------
# Hostile: the gateway is off
# ---------------------------------------------------------------------------

@_ASYNC
async def test_picoclaw_with_gateway_disabled_falls_back_visibly(mobile, monkeypatch, caplog):
    app, client = mobile
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "0")
    caplog.set_level(logging.INFO)
    resp = await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["framework"] == "opencode"
    cfg = await _framework(client)
    # The EFFECTIVE harness, never the preference.
    assert cfg["framework"] == "opencode"
    assert cfg["framework_preference"] == "picoclaw"
    assert cfg["framework_reason"] == FALLBACK_REASON
    assert await _lock_framework(client, monkeypatch) == "opencode"
    assert FALLBACK_REASON in caplog.text
    assert not _picoclaw_config_path(app).exists()
    assert _live_keys(app) == 0


@_ASYNC
async def test_auto_on_mobile_with_gateway_disabled_falls_back(mobile, monkeypatch, caplog):
    app, client = mobile
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "0")
    caplog.set_level(logging.INFO)
    resp = await client.put("/api/taos-agent/framework", json={"framework": "auto"})
    assert resp.status_code == 200, resp.text
    cfg = await _framework(client)
    assert cfg["framework"] == "opencode"
    assert cfg["framework_reason"] == FALLBACK_REASON
    assert FALLBACK_REASON in caplog.text


# ---------------------------------------------------------------------------
# Hostile: the key
# ---------------------------------------------------------------------------

@_ASYNC
async def test_key_never_in_a_response_or_a_log(mobile, monkeypatch, caplog):
    app, client = mobile
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    with respx.mock(assert_all_called=False) as router:
        router.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=_completion("ok")))
        resp = await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
        texts.append(resp.text)
        key = _key_from_config(app)
        texts.append((await client.get("/api/taos-agent/config")).text)
        texts.append((await client.get("/api/taos-agent/settings")).text)
        from tinyagentos.routes import auth as auth_mod
        monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)
        texts.append((await client.get("/auth/lock-widgets")).text)
        chat_text, _ = await _chat(client)
        texts.append(chat_text)
        from tinyagentos.cluster import model_resolver
        from tinyagentos.cluster.model_resolver import ModelLocation
        monkeypatch.setattr(model_resolver, "resolve_model_location",
                            lambda request, model_id: ModelLocation(kind="cloud"))
        resp = await client.put("/api/taos-agent/permitted-models",
                                json={"models": ["qwen3-8b", "gpt-small"]})
        texts.append(resp.text)
        new_key = _key_from_config(app)
        resp = await client.put("/api/taos-agent/framework", json={"framework": "opencode"})
        texts.append(resp.text)
    assert key.startswith("sk-taosgw-")
    for secret in (key, new_key):
        for t in texts:
            assert secret not in t
        assert secret not in caplog.text
        # Not even the random body of the key, masked or not.
        assert secret[-12:] not in caplog.text


@_ASYNC
async def test_switching_back_to_opencode_revokes_the_key(mobile):
    app, client = mobile
    resp = await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    assert resp.json()["framework"] == "picoclaw"
    key = _key_from_config(app)
    assert (await _models_with(app, key)).status_code == 200

    resp = await client.put("/api/taos-agent/framework", json={"framework": "opencode"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["framework"] == "opencode"
    after = await _models_with(app, key)
    assert after.status_code == 401, after.text
    # The dead key does not linger on disk either.
    assert not _picoclaw_config_path(app).exists()


@_ASYNC
async def test_leaving_mobile_by_device_class_override_revokes_too(desktop):
    """device.class is overridable; flipping a desktop host to mobile and
    back must mint and then revoke, like the framework override."""
    app, client = desktop
    resp = await client.put("/api/taos-agent/framework", json={"device_class": "mobile"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["framework"] == "picoclaw"
    key = _key_from_config(app)
    assert (await _models_with(app, key)).status_code == 200
    resp = await client.put("/api/taos-agent/framework", json={"device_class": "auto"})
    assert resp.json()["framework"] == "opencode"
    assert (await _models_with(app, key)).status_code == 401


@_ASYNC
async def test_a_restart_into_opencode_revokes_a_leftover_key(mobile):
    """An operator who edits config.yaml and restarts the controller (no API
    call) must not leave the PicoClaw key live."""
    app, client = mobile
    await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    key = _key_from_config(app)
    from tinyagentos.taos_agent_runtime import startup_framework_reconcile

    app.state.config.taos_agent = {"framework": "opencode"}
    startup_framework_reconcile(app.state)
    assert (await _models_with(app, key)).status_code == 401
    assert not _picoclaw_config_path(app).exists()


@_ASYNC
async def test_rendered_config_points_at_the_gateway_and_is_0600(mobile):
    app, client = mobile
    resp = await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    assert resp.status_code == 200, resp.text
    path = _picoclaw_config_path(app)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    cfg = json.loads(path.read_text())
    port = app.state.config.server["port"]
    defaults = cfg["agents"]["defaults"]
    assert defaults["model_name"] == "taos-default"
    assert defaults["restrict_to_workspace"] is True
    assert Path(defaults["workspace"]).resolve().is_relative_to(path.parent.resolve())
    default_entry = next(m for m in cfg["model_list"] if m["model_name"] == "taos-default")
    assert default_entry["api_base"] == f"http://127.0.0.1:{port}/api/llm/v1"
    assert default_entry["model"] == "openai/taos-default"
    key = default_entry["api_keys"][0]
    assert key.startswith("sk-taosgw-")
    for entry in cfg["model_list"]:
        assert entry["api_base"] == f"http://127.0.0.1:{port}/api/llm/v1"
    # The key may use taos-default and the agent's permitted models, nothing else.
    listed = {m["id"] for m in (await _models_with(app, key)).json()["data"]}
    assert listed == {"taos-default", "qwen3-8b"}


@_ASYNC
async def test_changing_permitted_models_re_mints_the_key(mobile, monkeypatch):
    app, client = mobile
    from tinyagentos.cluster import model_resolver
    from tinyagentos.cluster.model_resolver import ModelLocation
    monkeypatch.setattr(model_resolver, "resolve_model_location",
                        lambda request, model_id: ModelLocation(kind="cloud"))

    await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    old_key = _key_from_config(app)
    listed = {m["id"] for m in (await _models_with(app, old_key)).json()["data"]}
    assert "gpt-small" not in listed

    resp = await client.put("/api/taos-agent/permitted-models",
                            json={"models": ["qwen3-8b", "gpt-small"]})
    assert resp.status_code == 200, resp.text
    new_key = _key_from_config(app)
    assert new_key != old_key
    assert (await _models_with(app, old_key)).status_code == 401
    listed = {m["id"] for m in (await _models_with(app, new_key)).json()["data"]}
    assert listed == {"taos-default", "qwen3-8b", "gpt-small"}
    cfg = json.loads(_picoclaw_config_path(app).read_text())
    assert {m["model_name"] for m in cfg["model_list"]} >= {"taos-default", "gpt-small"}

    with respx.mock(assert_all_called=False) as router:
        route = router.post(UPSTREAM_CHAT).mock(
            return_value=httpx.Response(200, json=_completion("small", "gpt-small")))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {new_key}"}) as c:
            ok = await c.post(GATEWAY + "/chat/completions", json={
                "model": "gpt-small", "messages": [{"role": "user", "content": "x"}]})
    assert ok.status_code == 200, ok.text
    assert route.called


@_ASYNC
async def test_a_failed_turn_reports_an_error_not_silence(mobile):
    """The upstream refusing is an error frame the UI can show."""
    app, client = mobile
    await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    with respx.mock(assert_all_called=False) as router:
        router.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(500, json={"error": "boom"}))
        _, frames = await _chat(client)
    assert any("error" in f for f in frames), frames
    assert frames[-1] == {"done": True}


@_ASYNC
async def test_missing_picoclaw_binary_falls_back_to_opencode(mobile, monkeypatch, caplog):
    app, client = mobile
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.delenv("TAOS_PICOCLAW_BIN", raising=False)
    from tinyagentos import picoclaw_runtime
    monkeypatch.setattr(picoclaw_runtime, "_PICOCLAW_SYSTEM_PATHS", ())
    caplog.set_level(logging.INFO)
    resp = await client.put("/api/taos-agent/framework", json={"framework": "picoclaw"})
    assert resp.json()["framework"] == "opencode"
    cfg = await _framework(client)
    assert cfg["framework_preference"] == "picoclaw"
    assert "picoclaw binary not found" in cfg["framework_reason"]
    assert cfg["framework_reason"] in caplog.text
    assert _live_keys(app) == 0


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@_ASYNC
async def test_mobile_auto_is_picoclaw_everywhere(mobile, monkeypatch):
    app, client = mobile
    cfg = await _framework(client)
    assert cfg["device_class"] == "mobile"
    assert cfg["framework_preference"] == "picoclaw"
    assert cfg["framework"] == "picoclaw"
    assert await _lock_framework(client, monkeypatch) == "picoclaw"
    from tinyagentos.taos_agent_runtime import system_agent_framework
    assert system_agent_framework(app.state) == "picoclaw"


@_ASYNC
async def test_mobile_chat_turn_goes_through_picoclaw_and_the_gateway(mobile):
    app, client = mobile
    with respx.mock(assert_all_called=False) as router:
        route = router.post(UPSTREAM_CHAT).mock(
            return_value=httpx.Response(200, json=_completion("hi from the handset")))
        _, frames = await _chat(client, "what time is it")
    assert frames[-1] == {"done": True}
    reply = "".join(f.get("delta", "") for f in frames)
    assert reply == "hi from the handset", frames
    assert not any("error" in f for f in frames), frames
    assert "BANNER" not in reply

    # The gateway resolved taos-default to the agent's model and forwarded it.
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "qwen3-8b"
    assert sent["messages"][-1] == {"role": "user", "content": "what time is it"}
    assert route.calls.last.request.headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    # PicoClaw got the taOS Agent manual as its workspace AGENTS.md.
    assert "taOS" in sent["messages"][0]["content"]

    home = _picoclaw_config_path(app).parent
    calls = [json.loads(line) for line in (home / "fake-invocations.jsonl").read_text().splitlines()]
    last = calls[-1]
    assert last["argv"][0] == "agent"
    assert "-s" in last["argv"]
    assert Path(last["cwd"]).resolve() == (home / "workspace").resolve()
    assert last["config"] == str(_picoclaw_config_path(app))
    # A minimal environment: the controller's secrets are not inherited.
    assert "TAOS_LLM_GATEWAY" not in last["env"]


@_ASYNC
async def test_persona_reaches_picoclaw(mobile):
    app, client = mobile
    resp = await client.put("/api/taos-agent/persona", json={"persona": "You are Pip, terse."})
    assert resp.status_code == 200
    with respx.mock(assert_all_called=False) as router:
        route = router.post(UPSTREAM_CHAT).mock(
            return_value=httpx.Response(200, json=_completion("ok")))
        await _chat(client)
    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"][0]["content"].strip() == "You are Pip, terse."
