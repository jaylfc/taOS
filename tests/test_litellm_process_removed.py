"""LiteLLM removal stage 2b-2a (tsk-6i25yf): the LiteLLM process is gone.

Every agent's chat, stream and embedding already goes through the in-process
LLM gateway. These tests pin what removing the LiteLLM process must hold:

(a) a real controller startup spawns no LiteLLM process and binds nothing on
    the LiteLLM port;
(b) a container whose ``taos-proxy-litellm`` device still connects to a
    LiteLLM port is re-pointed to the gateway listener at startup;
(c) ``litellm`` is not in uv.lock (nor in a pyproject extra);
(d) agent key mint / re-scope / delete work with no LiteLLM process, through
    the real callers;
(e) spend is still recorded from the vendored price table through the gateway;
(f) a remote deploy is refused with a named reason;
(g) a new local deploy gets the in-container address ``127.0.0.1:4000``
    whatever ``litellm_port`` the host has.
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
import yaml
from httpx import ASGITransport, AsyncClient

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_litellm_argv(argv) -> bool:
    if isinstance(argv, (str, bytes)):
        argv = [argv]
    text = " ".join(str(a) for a in (argv or []))
    return "litellm" in text or "[proxy]" in text or "--extra proxy" in text


# ---------------------------------------------------------------------------
# (a) startup spawns no LiteLLM process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_spawns_no_litellm_process(tmp_path, monkeypatch):
    """Drive the REAL create_app + lifespan and record every subprocess the
    controller tries to start. Nothing may be LiteLLM (or a self-heal install
    of the proxy extra), no LiteLLM config dir may be written, and the
    configured LiteLLM port stays free."""
    litellm_port = _free_port()
    config = {
        "server": {"host": "0.0.0.0", "port": 6969, "litellm_port": litellm_port},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    spawned: list[str] = []
    real_popen = subprocess.Popen
    real_exec = asyncio.create_subprocess_exec

    class _FakeProc:
        pid = 999999
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    def recording_popen(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        if _is_litellm_argv(argv):
            spawned.append(f"Popen {argv!r}")
            return _FakeProc()  # never actually start LiteLLM from a test
        return real_popen(*args, **kwargs)

    async def recording_exec(*cmd, **kwargs):
        if _is_litellm_argv(cmd):
            spawned.append(f"create_subprocess_exec {cmd!r}")
            raise OSError("test: refusing to run a LiteLLM install")
        return await real_exec(*cmd, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_exec)

    from tinyagentos.app import create_app

    app = create_app(data_dir=tmp_path)
    async with app.router.lifespan_context(app):
        assert app.state._startup_complete is True
        # The old bring-up ran as a background task; give it time to act.
        for _ in range(60):
            if spawned:
                break
            await asyncio.sleep(0.1)

    assert spawned == [], f"startup tried to run LiteLLM: {spawned}"
    assert not (tmp_path / "litellm").exists(), "startup wrote a LiteLLM config dir"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", litellm_port))  # nothing holds the LiteLLM port


# ---------------------------------------------------------------------------
# (b) legacy proxy devices are re-pointed to the gateway at startup
# ---------------------------------------------------------------------------


class _FakeIncus:
    """``containers._run`` stand-in: one incus project, device connects in a dict."""

    def __init__(self, connects: dict[str, str]):
        self.connects = dict(connects)
        self.sets: list[tuple[str, str]] = []

    async def __call__(self, cmd, timeout=None):
        if cmd[:2] == ["incus", "list"]:
            return 0, json.dumps([{"name": n, "project": "default"} for n in self.connects])
        if cmd[:4] == ["incus", "config", "device", "get"]:
            name = cmd[4]
            return (0, self.connects[name] + "\n") if name in self.connects else (1, "not found")
        if cmd[:4] == ["incus", "config", "device", "set"]:
            name, kv = cmd[4], cmd[6]
            value = kv.split("=", 1)[1]
            self.connects[name] = value
            self.sets.append((name, value))
            return 0, ""
        return 1, f"unexpected incus call {cmd!r}"


def _state(tmp_path, agents, *, litellm_port=7834, gateway_port=7838):
    from tinyagentos.llm_proxy import LLMProxy

    return SimpleNamespace(
        data_dir=tmp_path,
        config=SimpleNamespace(agents=agents, backends=[], server={}),
        llm_proxy=LLMProxy(port=litellm_port, data_dir=tmp_path),
        llm_gateway_agent_port=gateway_port,
        llm_gateway_listener_identity="nonce-1",
    )


@pytest.mark.asyncio
async def test_startup_repoints_device_still_on_legacy_litellm_port(tmp_path, monkeypatch):
    """The Pi's orphan 'importtest' shape: connect tcp:127.0.0.1:4000 while the
    host's litellm_port is 7834. Today that is 'unexpected connect: left alone';
    with LiteLLM gone it must move to the gateway listener."""
    from tinyagentos import containers
    from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path
    from tinyagentos.llm_gateway import cutover

    key = LiteLLMKeyStore(default_keystore_path(tmp_path)).mint("importtest", ["default"])
    agents = [{"name": "importtest", "llm_key": key, "model": "default"}]
    incus = _FakeIncus({"taos-agent-importtest": "tcp:127.0.0.1:4000"})
    monkeypatch.setattr(containers, "_run", incus)
    monkeypatch.setattr(cutover, "wait_for_listener", AsyncMock(return_value=True))

    report = await cutover.run_startup_reconcile(_state(tmp_path, agents))

    assert incus.connects["taos-agent-importtest"] == "tcp:127.0.0.1:7838", report
    assert [i["agent"] for i in report["repointed"]] == ["importtest"]


@pytest.mark.asyncio
async def test_startup_repoints_litellm_device_even_when_key_cannot_mirror(tmp_path, monkeypatch):
    """No LiteLLM left to fall back to: a device on the configured LiteLLM port
    moves to the gateway even when its key is unknown to the local store. The
    problem is reported on the repointed item instead of stranding the agent."""
    from tinyagentos import containers
    from tinyagentos.llm_gateway import cutover

    agents = [{"name": "stranger", "llm_key": "sk-legacy-postgres-key", "model": "default"}]
    incus = _FakeIncus({"taos-agent-stranger": "tcp:127.0.0.1:7834"})
    monkeypatch.setattr(containers, "_run", incus)
    monkeypatch.setattr(cutover, "wait_for_listener", AsyncMock(return_value=True))

    report = await cutover.run_startup_reconcile(_state(tmp_path, agents))

    assert incus.connects["taos-agent-stranger"] == "tcp:127.0.0.1:7838", report
    item = report["repointed"][0]
    assert item["agent"] == "stranger" and item.get("reason")


@pytest.mark.asyncio
async def test_startup_never_moves_a_device_onto_an_unverified_listener(tmp_path, monkeypatch):
    """The one gate that stays: a listener this start could not verify as its
    own (dead, or a stranger on the port) is never handed an agent."""
    from tinyagentos import containers
    from tinyagentos.llm_gateway import cutover

    agents = [{"name": "careful", "llm_key": "sk-x", "model": "default"}]
    incus = _FakeIncus({"taos-agent-careful": "tcp:127.0.0.1:4000"})
    monkeypatch.setattr(containers, "_run", incus)
    monkeypatch.setattr(cutover, "wait_for_listener", AsyncMock(return_value=False))

    report = await cutover.run_startup_reconcile(_state(tmp_path, agents))

    assert incus.sets == []
    assert report["skipped"] and report["skipped"][0]["agent"] == "careful"


# ---------------------------------------------------------------------------
# (c) litellm is out of the lock and the extras
# ---------------------------------------------------------------------------


def test_litellm_absent_from_uv_lock_and_extras():
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    names = {p["name"] for p in lock.get("package", [])}
    assert "litellm" not in names
    assert "litellm-proxy-extras" not in names
    project = next(p for p in lock["package"] if p["name"] == "tinyagentos")
    # ``proxy`` survives only as an EMPTY extra so pre-#3313 updaters can run
    # ``uv sync --extra proxy`` (tests/test_update_extras_compat.py); it must pull nothing.
    assert not (project.get("optional-dependencies") or {}).get("proxy")
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    extras = pyproject["project"].get("optional-dependencies") or {}
    assert extras.get("proxy", []) == []
    assert not any("litellm" in req for reqs in extras.values() for req in reqs)
    assert not any("litellm" in req for req in pyproject["project"]["dependencies"])


# ---------------------------------------------------------------------------
# (d) key ops need no LiteLLM process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_re_mint_agent_key_works_with_no_process(tmp_path):
    """agent_keys.re_mint_agent_key (the openclaw route's caller) mints from
    the local store; today it returns None because LiteLLM is not running."""
    from tinyagentos.agent_keys import re_mint_agent_key
    from tinyagentos.config import load_config
    from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path
    from tinyagentos.llm_proxy import LLMProxy

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump({"server": {}, "backends": [], "agents": [{"name": "a1", "model": "m1"}]}))
    config = load_config(cfg_path)
    agent = config.agents[0]
    proxy = LLMProxy(port=7834, data_dir=tmp_path)

    key = await re_mint_agent_key("a1", agent, proxy, config, str(cfg_path))

    assert key and agent["llm_key"] == key
    rec = LiteLLMKeyStore(default_keystore_path(tmp_path)).lookup(key)
    assert rec["agent"] == "a1" and "m1" in rec["allowed_models"]


@pytest.mark.asyncio
async def test_rescope_and_delete_work_with_no_process(tmp_path):
    from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path
    from tinyagentos.llm_proxy import LLMProxy

    proxy = LLMProxy(port=7834, data_dir=tmp_path)
    key = await proxy.create_agent_key("a2", models=["m1"])
    assert await proxy.update_agent_key(key, ["m2"]) is True
    store = LiteLLMKeyStore(default_keystore_path(tmp_path))
    assert "m2" in store.lookup(key)["allowed_models"]
    assert await proxy.delete_agent_key(key) is True
    assert store.lookup(key) is None


@pytest.mark.asyncio
async def test_archive_restore_mints_key_with_no_process(client, app, monkeypatch):
    """The restore route mints the agent a new key and pushes it into the
    container. Today it is skipped because LiteLLM is not running."""
    from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

    env_calls = []

    async def fake_deploy(req):
        return {"success": True, "name": req.name, "ip": "10.0.0.56",
                "llm_key": None, "steps": [], "container": f"taos-agent-{req.name}"}

    async def ok(*a, **k):
        return {"success": True, "output": ""}

    async def fake_set_env(name, key, value):
        env_calls.append((name, key, value))
        return {"success": True, "output": ""}

    async def fake_exec(name, cmd, timeout=300):
        return (0, "")

    async def exists(name):
        return True

    monkeypatch.setattr("tinyagentos.containers.container_exists", exists)
    monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
    for fn in ("stop_container", "snapshot_create", "snapshot_restore", "start_container"):
        monkeypatch.setattr(f"tinyagentos.containers.{fn}", ok)
    monkeypatch.setattr("tinyagentos.containers.set_env", fake_set_env)
    monkeypatch.setattr("tinyagentos.containers.exec_in_container", fake_exec)

    await client.post("/api/agents/deploy", json={"name": "phoenix", "framework": "none"})
    await asyncio.sleep(0.2)
    await client.delete("/api/agents/phoenix")
    archive_id = (await client.get("/api/agents/archived")).json()[0]["id"]

    resp = await client.post(f"/api/agents/archived/{archive_id}/restore")

    assert resp.status_code == 200
    assert resp.json()["new_llm_key"] is True
    pushed = [v for _, k, v in env_calls if k == "OPENAI_API_KEY"]
    assert pushed, env_calls
    rec = LiteLLMKeyStore(default_keystore_path(app.state.data_dir)).lookup(pushed[0])
    assert rec is not None and rec["agent"] == "phoenix"


# ---------------------------------------------------------------------------
# (e) spend through the gateway, priced from the vendored table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_records_spend_from_price_table(tmp_path):
    from tinyagentos.agent_budget_store import AgentBudgetStore, default_budget_path
    from tinyagentos.app import create_app
    from tinyagentos.llm_usage.pricing import find_price

    upstream = "https://api.openai.com/v1"
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [{"name": "openai", "type": "openai", "url": upstream,
                      "models": [{"id": "gpt-4o-mini"}], "api_key": "sk-up", "priority": 1}],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    app.state._startup_complete = True
    key = await app.state.llm_proxy.create_agent_key("spender", models=["gpt-4o-mini"])

    usage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
    with respx.mock(assert_all_called=True) as mock:
        mock.post(f"{upstream}/chat/completions").mock(return_value=httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"},
                         "finish_reason": "stop"}],
            "usage": usage,
        }))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": f"Bearer {key}"}) as c:
            resp = await c.post("/api/llm/v1/chat/completions", json={
                "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]})
    assert resp.status_code == 200, resp.text

    _, entry = find_price("openai", "gpt-4o-mini")
    expected = 1000 * entry["input_cost_per_token"] + 500 * entry["output_cost_per_token"]
    spend = AgentBudgetStore(default_budget_path(tmp_path)).get("spender")
    assert spend is not None
    assert spend["spend_usd"] == pytest.approx(expected)
    await app.state.http_client.aclose()


# ---------------------------------------------------------------------------
# (f) remote deploy refused, (g) local deploy gets the in-container address
# ---------------------------------------------------------------------------


def _deploy_patches():
    async def mock_exec(name, cmd, **kwargs):
        return (0, "10.0.0.5") if "hostname -I" in " ".join(cmd) else (0, "ok")

    return (
        patch("tinyagentos.deployer.create_container", new_callable=AsyncMock,
              return_value={"success": True, "name": "taos-agent-x"}),
        patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec),
        patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")),
        patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock,
              return_value={"success": True, "output": ""}),
    )


@pytest.mark.asyncio
async def test_remote_deploy_refused_with_named_reason(tmp_path):
    from tinyagentos.deployer import DeployRequest, deploy_agent
    from tinyagentos.llm_proxy import LLMProxy

    req = DeployRequest(
        name="far", framework="smolagents", model="m1", data_dir=tmp_path,
        remote="fedora-worker", taos_host="100.64.0.1",
        extra_config={"llm_proxy": LLMProxy(port=7834, data_dir=tmp_path), "llm_gateway_port": 7838},
    )
    p_create, p_exec, p_push, p_dev = _deploy_patches()
    with p_create as create, p_exec, p_push, p_dev:
        result = await deploy_agent(req)

    assert result["success"] is False
    assert "remote agents need the network LLM gateway, not built yet" in result["error"]
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_deploy_env_uses_in_container_address(tmp_path):
    """The container reaches its LLM at its own 127.0.0.1:4000 (the proxy
    device's listen side). The host's litellm_port (7834 on fresh installs)
    is not reachable from inside the container and must never leak in."""
    from tinyagentos.deployer import DeployRequest, deploy_agent
    from tinyagentos.llm_proxy import LLMProxy

    req = DeployRequest(
        name="near", framework="smolagents", model="m1", data_dir=tmp_path,
        extra_config={
            "llm_proxy": LLMProxy(port=7834, data_dir=tmp_path),
            "llm_gateway_port": 7838,
            "llm_gateway_models_problem": AsyncMock(return_value=None),
        },
    )
    p_create, p_exec, p_push, p_dev = _deploy_patches()
    with p_create as create, p_exec, p_push, p_dev as dev:
        result = await deploy_agent(req)

    assert result["success"] is True, result
    env = create.call_args.kwargs["env"]
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:4000/v1"
    assert env["TAOS_EMBEDDING_URL"] == "http://127.0.0.1:4000/v1/embeddings"
    assert env["LITELLM_API_KEY"] and env["LITELLM_API_KEY"] == env["OPENAI_API_KEY"]
    llm_dev = next(c for c in dev.await_args_list if c.args[1] == "taos-proxy-litellm")
    assert llm_dev.kwargs["listen"] == "tcp:127.0.0.1:4000"
    assert llm_dev.kwargs["connect"] == "tcp:127.0.0.1:7838"
