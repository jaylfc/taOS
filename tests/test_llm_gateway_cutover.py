"""LiteLLM -> in-process gateway cutover (LiteLLM removal stage 2b-2a).

The gateway is the only LLM path; agents are moved onto it by retargeting the
incus proxy device behind their own ``127.0.0.1:4000`` (their base URL never
changes). A device still on an old LiteLLM port is moved UNCONDITIONALLY
(there is nothing to fall back to); when the agent's key is its own
``agent_keys`` row its gateway key is minted BEFORE the device moves; a stale
LiteLLM master key never opens the gateway; and a device is never moved onto a
listener this start could not verify.

The incus CLI is faked at ``tinyagentos.containers._run`` with a tiny model of
the device table, so every command the cutover issues is recorded and its
effect is visible to the next command (idempotency and rollback are measured,
not assumed).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path, token_hash

GATEWAY_PORT = 7838
# The Orange Pi predates the #795 port move: its LiteLLM listens on 4000, so
# the tests use 4000 (not the new-install 7834) and a hard-coded 7834 anywhere
# in the product shows up red.
LITELLM_PORT = 4000
DEVICE = "taos-proxy-litellm"
PROJECT = "user-999"


# ---------------------------------------------------------------------------
# Fake incus: a device table the commands read and write
# ---------------------------------------------------------------------------


class FakeIncus:
    """Just enough of ``incus`` for the cutover: list, device get, device set."""

    def __init__(self, containers: dict[str, str], connect: dict[str, str]):
        self.projects = dict(containers)  # container -> project
        self.connect = dict(connect)  # container -> current connect
        self.calls: list[list[str]] = []
        self.fail_get: set[str] = set()
        self.hang_get: set[str] = set()

    @staticmethod
    def _split(cmd):
        args, project = [], None
        it = iter(cmd)
        for a in it:
            if a == "--project":
                project = next(it)
            else:
                args.append(a)
        return args, project

    async def run(self, cmd, timeout=120):
        self.calls.append(list(cmd))
        args, project = self._split(cmd)
        if args[:2] == ["incus", "list"]:
            # Like real incus: without --all-projects only the ambient
            # (default) project is listed, so user-999 agents are invisible.
            visible = {n: p for n, p in self.projects.items()
                       if "--all-projects" in args or p == (project or "default")}
            return 0, json.dumps([{"name": n, "project": p} for n, p in visible.items()])
        if args[:4] == ["incus", "config", "device", "get"]:
            name, dev, key = args[4], args[5], args[6]
            if project != self.projects.get(name):
                return 1, f"Error: Failed to fetch instance {name!r} in project {project!r}: not found"
            if name in self.hang_get:
                raise TimeoutError(f"incus hung on {name}")
            if name in self.fail_get or dev != DEVICE or key != "connect":
                return 1, "Error: boom"
            return 0, self.connect[name] + "\n"
        if args[:4] == ["incus", "config", "device", "set"]:
            name, dev, kv = args[4], args[5], args[6]
            if project != self.projects.get(name):
                return 1, "Error: not found"
            key, _, value = kv.partition("=")
            assert dev == DEVICE and key == "connect", cmd
            self.connect[name] = value
            return 0, ""
        return 1, f"unexpected command {cmd}"

    def sets(self):
        return [c for c in self.calls if c[:4] == ["incus", "config", "device", "set"]]


def _agent_with_key(data_dir: Path, name: str, models=("gpt-a",)) -> dict:
    key = LiteLLMKeyStore(default_keystore_path(data_dir)).mint(name, list(models))
    return {"name": name, "llm_key": key}


def _request(data_dir: Path, bearer: str):
    return SimpleNamespace(
        state=SimpleNamespace(),
        headers={"authorization": f"Bearer {bearer}"},
        app=SimpleNamespace(state=SimpleNamespace(data_dir=data_dir)),
    )


async def _all_routable(models):
    return None


async def _reconcile(fake, agents, data_dir, *, ready=True, models_problem=_all_routable):
    from tinyagentos.llm_gateway import cutover

    with patch("tinyagentos.containers._run", side_effect=fake.run):
        return await cutover.reconcile_agents(
            agents=agents,
            data_dir=data_dir,
            gateway_port=GATEWAY_PORT,
            legacy_ports=[LITELLM_PORT, 7834],
            listener_ready=ready,
            models_problem=models_problem,
        )


def _stale_master(data_dir: Path) -> str:
    """Old installs still have the LiteLLM master key file on disk."""
    master = "sk-taos-stale-litellm-master-key-0123456789"
    (Path(data_dir) / ".litellm_master_key").write_text(master)
    return master


# ---------------------------------------------------------------------------
# RED-FIRST 1: with no config, the gateway routes are mounted and answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_is_mounted_and_answers_with_no_flag_set(tmp_data_dir, monkeypatch):
    from tinyagentos.app import create_app

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    token = app.state.auth.get_local_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        resp = await c.get("/api/llm/v1/models")
    assert resp.status_code == 200, resp.text
    assert resp.json()["object"] == "list"


# ---------------------------------------------------------------------------
# RED-FIRST 2: an existing agent's LiteLLM key authenticates after migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_agent_litellm_key_authenticates_on_the_gateway_after_migration(tmp_path):
    from tinyagentos.llm_gateway.auth import gateway_caller

    agent = _agent_with_key(tmp_path, "naira", ["gpt-a"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    report = await _reconcile(fake, [agent], tmp_path)

    assert [r["agent"] for r in report["repointed"]] == ["naira"]
    caller = gateway_caller(_request(tmp_path, agent["llm_key"]))
    assert caller.caller_id == "naira"
    assert caller.kind == "agent"
    assert caller.allowed_models == frozenset({"gpt-a"})
    # It is the MINTED gateway key that authenticates, not the legacy row.
    assert caller.key_id is not None and caller.key_id.startswith("gk_lit_")


# ---------------------------------------------------------------------------
# RED-FIRST 3: the master key alone is refused by the gateway
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_master_key_alone_is_refused_by_the_gateway(tmp_data_dir, monkeypatch):
    from tinyagentos.app import create_app

    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    master = _stale_master(tmp_data_dir)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {master}"}) as c:
        models = await c.get("/api/llm/v1/models")
        chat = await c.post("/api/llm/v1/chat/completions",
                            json={"model": "taos-default",
                                  "messages": [{"role": "user", "content": "hi"}]})
    assert models.status_code == 401, models.text
    assert chat.status_code == 401, chat.text
    assert models.json()["error"]["code"] == "invalid_api_key"


def test_master_key_maps_to_no_caller(tmp_path):
    from tinyagentos.llm_gateway.auth import gateway_caller
    from tinyagentos.llm_gateway.errors import GatewayError

    master = _stale_master(tmp_path)
    with pytest.raises(GatewayError) as exc:
        gateway_caller(_request(tmp_path, master))
    assert exc.value.status == 401


# ---------------------------------------------------------------------------
# RED-FIRST 4: a container proxy-device update targets the gateway port
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_repoints_the_proxy_device_at_the_gateway_port_in_the_agents_project(tmp_path):
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT, "taos-agent-other": "default"},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}",
                      "taos-agent-other": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    await _reconcile(fake, [agent], tmp_path)

    assert fake.sets() == [[
        "incus", "config", "device", "set", "taos-agent-naira", DEVICE,
        f"connect=tcp:127.0.0.1:{GATEWAY_PORT}", "--project", PROJECT,
    ]]
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    # Only agents in config are touched.
    assert fake.connect["taos-agent-other"] == f"tcp:127.0.0.1:{LITELLM_PORT}"


async def _deploy(tmp_path, monkeypatch, extra):
    from tinyagentos.deployer import DeployRequest, deploy_agent

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    store = LiteLLMKeyStore(default_keystore_path(tmp_path))
    proxy = MagicMock(spec=["port", "create_agent_key"])
    proxy.port = LITELLM_PORT
    proxy.create_agent_key = AsyncMock(side_effect=lambda name, models=None: store.mint(name, models or ["default"]))
    req = DeployRequest(name="fresh", framework="smolagents", model="gpt-a", data_dir=tmp_path,
                        extra_config={"llm_proxy": proxy, **extra})

    async def mock_exec(name, cmd, **kwargs):
        return (0, "10.0.0.5") if "hostname -I" in " ".join(cmd) else (0, "ok")

    with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create, \
         patch("tinyagentos.deployer.exec_in_container", side_effect=mock_exec), \
         patch("tinyagentos.deployer.push_file", new_callable=AsyncMock, return_value=(0, "")), \
         patch("tinyagentos.deployer.add_proxy_device", new_callable=AsyncMock) as dev:
        mock_create.return_value = {"success": True, "name": "taos-agent-fresh"}
        dev.return_value = {"success": True, "output": ""}
        result = await deploy_agent(req)
    assert result["success"] is True, result
    call = next(c for c in dev.call_args_list if c.args[1] == DEVICE)
    assert call.kwargs["listen"] == "tcp:127.0.0.1:4000"
    return result, call.kwargs["connect"]


@pytest.mark.asyncio
async def test_new_deploy_attaches_its_proxy_device_to_the_gateway_port(tmp_path, monkeypatch):
    checked = []

    async def routable(models):
        checked.append(list(models))
        return None

    result, connect = await _deploy(tmp_path, monkeypatch, {
        "llm_gateway_port": GATEWAY_PORT, "llm_gateway_models_problem": routable})
    assert connect == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert checked == [["gpt-a"]]
    # The key it was handed authenticates on the gateway as this agent.
    from tinyagentos.llm_gateway.auth import gateway_caller
    caller = gateway_caller(_request(tmp_path, result["llm_key"]))
    assert caller.caller_id == "fresh" and caller.key_id.startswith("gk_lit_")


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["unroutable", "missing"])
async def test_new_deploy_on_an_unforwardable_model_still_goes_to_the_gateway(tmp_path, monkeypatch, check):
    """No LiteLLM to stay on: the device targets the gateway and the deploy
    records a warning step naming the problem."""
    async def unroutable(models):
        return "model 'gpt-a' is not in the routing table"

    extra = {"llm_gateway_port": GATEWAY_PORT}
    if check == "unroutable":
        extra["llm_gateway_models_problem"] = unroutable
    result, connect = await _deploy(tmp_path, monkeypatch, extra)
    assert connect == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert any(s.startswith("llm: warning: the gateway cannot serve its models yet") for s in result["steps"])


@pytest.mark.asyncio
async def test_new_deploy_without_a_verified_listener_is_refused(tmp_path, monkeypatch):
    from tinyagentos.deployer import DeployRequest, deploy_agent

    proxy = MagicMock(spec=["port", "create_agent_key"])
    proxy.port = LITELLM_PORT
    proxy.create_agent_key = AsyncMock(return_value="sk-x")
    req = DeployRequest(name="early", framework="smolagents", model="gpt-a", data_dir=tmp_path,
                        extra_config={"llm_proxy": proxy, "llm_gateway_port": 0})
    with patch("tinyagentos.deployer.create_container", new_callable=AsyncMock) as mock_create:
        result = await deploy_agent(req)
    assert result["success"] is False
    assert "listener is not verified" in result["error"]
    mock_create.assert_not_awaited()
    proxy.create_agent_key.assert_not_awaited()


# ---------------------------------------------------------------------------
# RED-FIRST 5: otel/judge.py's default base URL points at the gateway
# ---------------------------------------------------------------------------


def test_judge_default_base_url_points_at_the_gateway():
    from tinyagentos.otel.judge import ReasoningJudge

    judge = ReasoningJudge(litellm_api_key="k")
    assert judge._base_url == "http://127.0.0.1:6969/api/llm/v1"


# ---------------------------------------------------------------------------
# Migration safety (green-only; each guards a named requirement)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    await _reconcile(fake, [agent], tmp_path)
    first_sets = len(fake.sets())
    report = await _reconcile(fake, [agent], tmp_path)
    assert len(fake.sets()) == first_sets == 1
    assert report["repointed"] == []
    assert [r["agent"] for r in report["unchanged"]] == ["naira"]


@pytest.mark.asyncio
async def test_there_is_no_rollback_to_litellm(tmp_path):
    """A device already on the gateway stays there on every later start, even
    with the listener unverified: there is no LiteLLM to go back to."""
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    await _reconcile(fake, [agent], tmp_path)
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    n = len(fake.sets())
    report = await _reconcile(fake, [agent], tmp_path, ready=False)
    assert len(fake.sets()) == n
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert [r["agent"] for r in report["unchanged"]] == ["naira"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [4000, 7834])
async def test_every_legacy_litellm_port_is_moved(tmp_path, legacy):
    """4000 (pre-#795 installs) and 7834 (later default) are both dead ports."""
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{legacy}"})
    report = await _reconcile(fake, [agent], tmp_path)
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert report["repointed"][0]["from"] == f"tcp:127.0.0.1:{legacy}"


@pytest.mark.asyncio
async def test_key_is_minted_before_the_device_moves(tmp_path):
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    seen = {}
    real_run = fake.run

    async def run(cmd, timeout=120):
        if cmd[:4] == ["incus", "config", "device", "set"]:
            rec = LiteLLMKeyStore(default_keystore_path(tmp_path)).gateway_key_by_hash(
                token_hash(agent["llm_key"]))
            seen["row_at_set"] = rec
        return await real_run(cmd, timeout)

    fake.run = run
    await _reconcile(fake, [agent], tmp_path)
    rec = seen["row_at_set"]
    assert rec is not None and rec["bound_to"] == "naira" and rec["revoked_ts"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_key", "master_key", "unknown_key", "other_agents_key"])
async def test_agent_whose_key_cannot_be_read_is_still_moved_and_reported(tmp_path, case):
    """Its device points at a dead LiteLLM port, so it moves anyway; the key
    problem is named on its item and NOTHING is minted for it (an unscoped or
    borrowed key is never mirrored)."""
    other = _agent_with_key(tmp_path, "mary")
    agent = {"name": "naira"}
    if case == "master_key":
        agent["llm_key"] = _stale_master(tmp_path)
    elif case == "unknown_key":
        agent["llm_key"] = "sk-" + "x" * 40
    elif case == "other_agents_key":
        agent["llm_key"] = other["llm_key"]
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    before = LiteLLMKeyStore(default_keystore_path(tmp_path))
    with before._connect() as conn:
        rows_before = conn.execute("SELECT COUNT(*) FROM gateway_keys").fetchone()[0]

    report = await _reconcile(fake, [agent], tmp_path)

    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert [r["agent"] for r in report["repointed"]] == ["naira"]
    assert "re-key or redeploy" in report["repointed"][0]["reason"]
    with before._connect() as conn:
        rows_after = conn.execute("SELECT COUNT(*) FROM gateway_keys").fetchone()[0]
    assert rows_after == rows_before  # nothing minted, scoped or otherwise


@pytest.mark.asyncio
async def test_remote_agent_is_skipped_by_name(tmp_path):
    agent = dict(_agent_with_key(tmp_path, "naira"), remote="worker-1")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    report = await _reconcile(fake, [agent], tmp_path)
    assert fake.sets() == []
    assert "remote agents need the network LLM gateway" in report["skipped"][0]["reason"]


@pytest.mark.asyncio
async def test_unreadable_device_is_left_alone(tmp_path):
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    fake.fail_get.add("taos-agent-naira")
    report = await _reconcile(fake, [agent], tmp_path)
    assert fake.sets() == []
    assert report["skipped"][0]["agent"] == "naira"


@pytest.mark.asyncio
async def test_unexpected_connect_target_is_left_alone(tmp_path):
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT}, {"taos-agent-naira": "tcp:127.0.0.1:9999"})
    report = await _reconcile(fake, [agent], tmp_path)
    assert fake.sets() == []
    assert report["skipped"][0]["agent"] == "naira"


@pytest.mark.asyncio
async def test_listener_not_ready_moves_nobody(tmp_path):
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    report = await _reconcile(fake, [agent], tmp_path, ready=False)
    assert fake.sets() == []
    assert report["skipped"][0]["agent"] == "naira"


@pytest.mark.asyncio
async def test_missing_container_is_skipped(tmp_path):
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({}, {})
    report = await _reconcile(fake, [agent], tmp_path)
    assert fake.sets() == []
    assert report["skipped"][0]["agent"] == "naira"


# ---------------------------------------------------------------------------
# Mirror row follows the legacy row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescope_and_delete_follow_the_minted_key(tmp_path):
    from tinyagentos.llm_gateway.auth import gateway_caller
    from tinyagentos.llm_gateway.errors import GatewayError

    agent = _agent_with_key(tmp_path, "naira", ["gpt-a"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    await _reconcile(fake, [agent], tmp_path)
    store = LiteLLMKeyStore(default_keystore_path(tmp_path))
    assert store.set_models(agent["llm_key"], ["gpt-b"]) is True
    assert gateway_caller(_request(tmp_path, agent["llm_key"])).allowed_models == frozenset({"gpt-b"})
    assert store.delete(agent["llm_key"]) is True
    with pytest.raises(GatewayError):
        gateway_caller(_request(tmp_path, agent["llm_key"]))


# ---------------------------------------------------------------------------
# The agent-facing listener: /v1 -> gateway, the rest -> 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/models", "/models"])
async def test_listener_serves_the_gateway_at_the_agents_base_url(tmp_data_dir, monkeypatch, path):
    from tinyagentos.app import create_app
    from tinyagentos.llm_gateway.listener import create_agent_listener_app

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    agent = _agent_with_key(tmp_data_dir, "naira", ["gpt-a"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    await _reconcile(fake, [agent], tmp_data_dir)
    listener = create_agent_listener_app(app)
    async with AsyncClient(transport=ASGITransport(app=listener), base_url="http://127.0.0.1:4000") as c:
        ok = await c.get(path, headers={"Authorization": f"Bearer {agent['llm_key']}"})
        bad = await c.get(path, headers={"Authorization": "Bearer sk-taos-nope-nope-nope-nope"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["object"] == "list"
    assert bad.status_code == 401


def _listener_with_fakes(identity=None):
    """The listener around a fake main app, recording every request that
    reached it (and at which path)."""
    from starlette.responses import JSONResponse

    from tinyagentos.llm_gateway.listener import create_agent_listener_app

    seen = {"main": []}

    async def main_app(scope, receive, send):
        seen["main"].append(scope["path"])
        await JSONResponse({"from": "gateway"})(scope, receive, send)

    kw = {"identity": identity} if identity else {}
    return create_agent_listener_app(main_app, **kw), None, seen


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("POST", "/key/generate"), ("POST", "/model/new"), ("POST", "/config/update"),
    ("POST", "/user/new"), ("POST", "/v1/messages"), ("POST", "/v1/responses"),
    ("GET", "/api/agents"), ("POST", "/api/system/prepare-shutdown"),
    ("POST", "/v1/completions"), ("GET", "/health"), ("POST", "/v1//embeddings"),
    ("POST", "/v1/../key/generate"),
])
async def test_listener_refuses_everything_it_does_not_serve(method, path):
    """Nothing but the gateway's three routes is served: LiteLLM's old admin API, /v1/messages and /v1/responses (which skip the gateway's
    keys, allowlists and budgets) and every controller route are a 404."""
    listener, _, seen = _listener_with_fakes()
    async with AsyncClient(transport=ASGITransport(app=listener), base_url="http://127.0.0.1:4000") as c:
        resp = await c.request(method, path, json={"model": "x"},
                               headers={"Authorization": "Bearer sk-taos-agentkey"})
    assert resp.status_code == 404, (path, resp.text)
    assert seen == {"main": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("path,where,canonical", [
    ("/v1/chat/completions/", "main", "/api/llm/v1/chat/completions"),
    ("/chat/completions//", "main", "/api/llm/v1/chat/completions"),
    ("/v1/models/", "main", "/api/llm/v1/models"),
    ("/v1/embeddings/", "main", "/api/llm/v1/embeddings"),
    ("/embeddings", "main", "/api/llm/v1/embeddings"),
])
async def test_listener_normalises_trailing_slashes(path, where, canonical):
    """A trailing slash must not route around the gateway (and its budgets)."""
    listener, _, seen = _listener_with_fakes()
    method = "GET" if "models" in path else "POST"
    async with AsyncClient(transport=ASGITransport(app=listener), base_url="http://127.0.0.1:4000") as c:
        resp = await c.request(method, path, json={"input": "x"})
    assert resp.status_code == 200, resp.text
    assert seen[where] == [canonical]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/embeddings", "/v1/chat/completions"])
async def test_listener_bounds_the_request_body(path, monkeypatch):
    from tinyagentos.llm_gateway import listener as mod

    monkeypatch.setattr(mod, "MAX_BODY_BYTES", 1024)
    listener, _, seen = _listener_with_fakes()
    async with AsyncClient(transport=ASGITransport(app=listener), base_url="http://127.0.0.1:4000") as c:
        big = await c.post(path, content=b"x" * 2048, headers={"content-type": "application/json"})

        async def chunks():  # no content-length: the count must still hold
            for _ in range(4):
                yield b"y" * 512

        streamed = await c.post(path, content=chunks(), headers={"content-type": "application/json"})
        small = await c.post(path, json={"input": "x"})
    assert big.status_code == 413 and streamed.status_code == 413
    assert small.status_code == 200
    assert len(seen["main"]) == 1


@pytest.mark.asyncio
async def test_listener_stamps_its_identity_on_every_response():
    listener, _, _ = _listener_with_fakes(identity="nonce-123")
    async with AsyncClient(transport=ASGITransport(app=listener), base_url="http://127.0.0.1:4000") as c:
        for resp in (await c.get("/v1/models"), await c.post("/v1/embeddings", json={}),
                     await c.post("/key/generate")):
            assert resp.headers.get("x-taos-llm-listener") == "nonce-123"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/embeddings", "/embeddings", "/v1/embeddings/"])
async def test_listener_serves_embeddings_from_the_gateway(path):
    """/v1/embeddings (TAOS_EMBEDDING_URL) is the gateway's: the main app gets
    it at /api/llm/v1/embeddings. There is no other upstream to reach."""
    listener, _, seen = _listener_with_fakes()
    async with AsyncClient(transport=ASGITransport(app=listener), base_url="http://127.0.0.1:4000") as c:
        resp = await c.post(path, json={"input": "hi", "model": "taos-embedding-default"},
                            headers={"Authorization": "Bearer sk-taos-agentkey"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"from": "gateway"}
    assert seen == {"main": ["/api/llm/v1/embeddings"]}


# ---------------------------------------------------------------------------
# The live invoker: run_startup_reconcile (what the controller runs at boot)
# ---------------------------------------------------------------------------


# The Orange Pi's two live agents run OpenRouter models; an NPU box runs
# rkllama (Ollama-shaped, serves /v1/chat/completions at the pinned ref);
# deepseek is OpenAI-compatible. Every configured chat provider is servable,
# so the unservable case is a model no backend serves any more.
BACKENDS = [
    {"name": "or-cloud", "type": "openrouter", "url": "https://openrouter.ai/api/v1",
     "models": [{"id": "tencent/hy3:free"}, {"id": "nvidia/nemotron-3-ultra-550b-a55b:free"}],
     "api_key": "sk-or-test", "priority": 2},
    {"name": "compat", "type": "openai-compatible", "url": "http://compat.test/v1",
     "models": [{"id": "gpt-a"}], "priority": 3},
    {"name": "npu", "type": "rkllama", "url": "http://localhost:8080", "priority": 1},
    {"name": "ds", "type": "deepseek", "url": "https://api.deepseek.com",
     "models": [{"id": "deepseek-chat"}], "api_key": "sk-ds", "priority": 4},
]


class _Prefs:
    def __init__(self, model=None):
        self.model = model

    async def get_preference(self, *key):
        return {"model": self.model} if self.model else {}


def _state(data_dir, agents, **extra):
    return SimpleNamespace(
        data_dir=data_dir,
        config=SimpleNamespace(agents=agents, server={}, backends=list(BACKENDS)),
        llm_proxy=SimpleNamespace(port=LITELLM_PORT),
        registry=None,
        desktop_settings=_Prefs(),
        **extra,
    )


async def _ready(port, **kw):
    return True


@pytest.mark.asyncio
async def test_startup_reconcile_is_a_noop_without_the_listener_port(tmp_path):
    from tinyagentos.llm_gateway.cutover import run_startup_reconcile

    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        assert await run_startup_reconcile(_state(tmp_path, [agent])) is None
    assert fake.calls == []


@pytest.mark.asyncio
async def test_startup_reconcile_moves_agents_once_the_listener_answers(tmp_path, monkeypatch):
    from tinyagentos.llm_gateway import cutover

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    agent = _agent_with_key(tmp_path, "naira", ["tencent/hy3:free"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    state = _state(tmp_path, [agent], llm_gateway_agent_port=GATEWAY_PORT,
                   llm_gateway_listener_identity="nonce-1")
    probed = []

    async def ready(port, **kw):
        probed.append((port, kw.get("identity")))
        return True

    monkeypatch.setattr(cutover, "wait_for_listener", ready)
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await cutover.run_startup_reconcile(state)
    assert probed == [(GATEWAY_PORT, "nonce-1")]
    assert state.llm_gateway_listener_ready is True
    assert cutover.llm_gateway_live_port(state) == GATEWAY_PORT
    assert [r["agent"] for r in report["repointed"]] == ["naira"]
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"


@pytest.mark.asyncio
async def test_startup_reconcile_with_the_old_off_flag_still_moves_agents(tmp_path, monkeypatch):
    """TAOS_LLM_GATEWAY=0 used to roll every agent back to LiteLLM. LiteLLM is
    gone, so the flag is a logged no-op and the forward move still happens."""
    from tinyagentos.llm_gateway import cutover

    monkeypatch.setenv("TAOS_LLM_GATEWAY", "0")
    monkeypatch.setattr(cutover, "wait_for_listener", _ready)
    agent = _agent_with_key(tmp_path, "naira", ["tencent/hy3:free"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    state = _state(tmp_path, [agent], llm_gateway_agent_port=GATEWAY_PORT,
                   llm_gateway_listener_identity="nonce-1")
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await cutover.run_startup_reconcile(state)
    assert state.llm_gateway_listener_ready is True
    assert [r["agent"] for r in report["repointed"]] == ["naira"]
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"


@pytest.mark.asyncio
async def test_startup_reconcile_moves_a_device_on_the_configured_litellm_port(tmp_path, monkeypatch):
    """A non-default server.litellm_port (here 4123) is a dead LiteLLM port too."""
    from tinyagentos.llm_gateway import cutover

    monkeypatch.setattr(cutover, "wait_for_listener", _ready)
    agent = _agent_with_key(tmp_path, "naira", ["tencent/hy3:free"])
    fake = FakeIncus({"taos-agent-naira": PROJECT}, {"taos-agent-naira": "tcp:127.0.0.1:4123"})
    state = _state(tmp_path, [agent], llm_gateway_agent_port=GATEWAY_PORT,
                   llm_gateway_listener_identity="nonce-1")
    state.llm_proxy = SimpleNamespace(port=4123)
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        await cutover.run_startup_reconcile(state)
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"


@pytest.mark.asyncio
async def test_startup_reconcile_with_a_dead_listener_moves_nobody(tmp_path, monkeypatch):
    from tinyagentos.llm_gateway import cutover

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    agent = _agent_with_key(tmp_path, "naira", ["tencent/hy3:free"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    state = _state(tmp_path, [agent], llm_gateway_agent_port=GATEWAY_PORT,
                   llm_gateway_listener_identity="nonce-1")

    async def dead(port, **kw):
        return False

    monkeypatch.setattr(cutover, "wait_for_listener", dead)
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await cutover.run_startup_reconcile(state)
    assert fake.sets() == []
    assert cutover.llm_gateway_live_port(state) == 0
    assert report["skipped"][0]["agent"] == "naira"


@pytest.mark.asyncio
async def test_agent_already_on_an_unverified_listener_is_left_on_the_gateway_port(tmp_path, monkeypatch):
    """Last start moved naira; this start the listener was not verified. There
    is no LiteLLM to send it back to, so its device is left as it is (it
    works again once the listener is back) and nothing new is moved on."""
    from tinyagentos.llm_gateway import cutover

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    agent = _agent_with_key(tmp_path, "naira", ["tencent/hy3:free"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{GATEWAY_PORT}"})
    state = _state(tmp_path, [agent], llm_gateway_agent_port=GATEWAY_PORT,
                   llm_gateway_listener_identity="nonce-1")

    async def dead(port, **kw):
        return False

    monkeypatch.setattr(cutover, "wait_for_listener", dead)
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await cutover.run_startup_reconcile(state)
    assert fake.sets() == []
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert [r["agent"] for r in report["unchanged"]] == ["naira"]


# ---------------------------------------------------------------------------
# Listener identity: only OUR listener counts as ready
# ---------------------------------------------------------------------------


async def _http_stranger(status: int, body: dict, headers: dict | None = None):
    """A raw HTTP server on a free loopback port answering every request alike."""
    import asyncio

    payload = json.dumps(body).encode()
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())

    async def handle(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
        except Exception:  # noqa: BLE001
            pass
        writer.write(
            f"HTTP/1.1 {status} X\r\ncontent-type: application/json\r\n{extra}"
            f"content-length: {len(payload)}\r\nconnection: close\r\n\r\n".encode() + payload
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,headers", [
    # e.g. an unauthenticated MLX server on the port: 200 to anyone.
    (200, {"object": "list", "data": [{"id": "mlx-model"}]}, None),
    # LiteLLM itself, or anything else that speaks OpenAI auth errors.
    (401, {"error": {"message": "no", "type": "invalid_request_error", "code": "invalid_api_key"}}, None),
    # Someone echoing our header name but not our nonce.
    (401, {"error": {"message": "no", "type": "invalid_request_error", "code": "invalid_api_key"}},
     {"x-taos-llm-listener": "not-the-nonce"}),
], ids=["open-200", "litellm-401", "wrong-nonce"])
async def test_a_stranger_on_the_listener_port_moves_no_agent(tmp_path, monkeypatch, status, body, headers):
    from tinyagentos.llm_gateway import cutover

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    monkeypatch.setattr(cutover, "_PROBE_ATTEMPTS", 2, raising=False)
    monkeypatch.setattr(cutover, "_PROBE_DELAY", 0.01, raising=False)
    server, port = await _http_stranger(status, body, headers)
    agent = _agent_with_key(tmp_path, "naira", ["tencent/hy3:free"])
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    state = _state(tmp_path, [agent], llm_gateway_agent_port=port,
                   llm_gateway_listener_identity="the-real-nonce")
    try:
        with patch("tinyagentos.containers._run", side_effect=fake.run):
            report = await cutover.run_startup_reconcile(state)
    finally:
        server.close()
        await server.wait_closed()
    assert fake.sets() == []
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{LITELLM_PORT}"
    assert cutover.llm_gateway_live_port(state) == 0  # new deploys stay off it too
    assert report["skipped"][0]["agent"] == "naira"


@pytest.mark.asyncio
async def test_wait_for_listener_recognises_our_listener_and_nothing_else(tmp_data_dir, monkeypatch):
    """End to end over a real socket: uvicorn serving the real listener
    around the real app is recognised; the same port with the wrong nonce,
    a bare TCP accept, and a closed port are not."""
    import asyncio
    import socket

    import uvicorn

    from tinyagentos.app import create_app
    from tinyagentos.llm_gateway.cutover import wait_for_listener
    from tinyagentos.llm_gateway.listener import create_agent_listener_app

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_agent_listener_app(app, identity="nonce-xyz"),
        host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert await wait_for_listener(port, identity="nonce-xyz", attempts=3, delay=0.05) is True
        assert await wait_for_listener(port, identity="other", attempts=2, delay=0.01) is False
        assert await wait_for_listener(port, identity=None, attempts=2, delay=0.01) is False
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)

    bare = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    bare_port = bare.sockets[0].getsockname()[1]
    try:
        assert await wait_for_listener(bare_port, identity="nonce-xyz", attempts=2, delay=0.01) is False
    finally:
        bare.close()
        await bare.wait_closed()
    assert await wait_for_listener(bare_port, identity="nonce-xyz", attempts=2, delay=0.01) is False
    assert await wait_for_listener(0, identity="nonce-xyz") is False


# ---------------------------------------------------------------------------
# Routability: an agent moves only if the gateway can serve every model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("models,ok", [
    (["tencent/hy3:free"], True),
    (["tencent/hy3:free", "nvidia/nemotron-3-ultra-550b-a55b:free"], True),
    (["gpt-a"], True),
    (["default"], True),                             # highest priority: rkllama (/v1 chat)
    (["deepseek-chat"], True),                       # OpenAI-compatible
    (["tencent/hy3:free", "deepseek-chat"], True),
    (["tencent/hy3:free", "no-such-model"], False),  # EVERY model must be servable
    (["no-such-model"], False),
    ([], False),
    (["taos-default"], False),                       # nothing behind it
])
async def test_models_problem(tmp_path, models, ok):
    from tinyagentos.llm_gateway.cutover import models_problem

    problem = await models_problem(_state(tmp_path, []), models)
    assert (problem is None) is ok, problem


@pytest.mark.asyncio
async def test_models_problem_follows_taos_default(tmp_path):
    from tinyagentos.llm_gateway.cutover import models_problem

    state = _state(tmp_path, [])
    state.desktop_settings = _Prefs("tencent/hy3:free")
    assert await models_problem(state, ["taos-default"]) is None
    state.desktop_settings = _Prefs("gone-model")
    assert await models_problem(state, ["taos-default"]) is not None


@pytest.mark.asyncio
async def test_agent_on_an_unforwardable_model_still_moves_with_a_reason(tmp_path, monkeypatch):
    """naira on OpenRouter moves; agents on models no backend serves any more
    move too (their LiteLLM port is dead), each with the reason on its item."""
    from tinyagentos.llm_gateway import cutover

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    monkeypatch.setattr(cutover, "wait_for_listener", _ready)
    agents = [
        _agent_with_key(tmp_path, "naira", ["tencent/hy3:free"]),
        _agent_with_key(tmp_path, "npu", ["gone-npu-model"]),
        _agent_with_key(tmp_path, "ds", ["gone-ds-model"]),
    ]
    names = {f"taos-agent-{a['name']}": PROJECT for a in agents}
    fake = FakeIncus(names, {n: f"tcp:127.0.0.1:{LITELLM_PORT}" for n in names})
    state = _state(tmp_path, agents, llm_gateway_agent_port=GATEWAY_PORT,
                   llm_gateway_listener_identity="n")
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await cutover.run_startup_reconcile(state)
    moved = {r["agent"]: r.get("reason") for r in report["repointed"]}
    assert set(moved) == {"naira", "npu", "ds"}
    assert moved["naira"] is None
    assert "gone-npu-model" in moved["npu"] and "gone-ds-model" in moved["ds"]
    assert report["skipped"] == []
    for name in ("naira", "npu", "ds"):
        assert fake.connect[f"taos-agent-{name}"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"


@pytest.mark.asyncio
async def test_agent_on_the_gateway_whose_model_became_unforwardable_stays(tmp_path, monkeypatch):
    """There is no LiteLLM to go back to: it stays on the gateway (logged)."""
    from tinyagentos.llm_gateway import cutover

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    monkeypatch.setattr(cutover, "wait_for_listener", _ready)
    agent = _agent_with_key(tmp_path, "npu", ["gone-npu-model"])
    fake = FakeIncus({"taos-agent-npu": PROJECT}, {"taos-agent-npu": f"tcp:127.0.0.1:{GATEWAY_PORT}"})
    state = _state(tmp_path, [agent], llm_gateway_agent_port=GATEWAY_PORT,
                   llm_gateway_listener_identity="n")
    with patch("tinyagentos.containers._run", side_effect=fake.run):
        report = await cutover.run_startup_reconcile(state)
    assert fake.sets() == []
    assert fake.connect["taos-agent-npu"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert [r["agent"] for r in report["unchanged"]] == ["npu"]


@pytest.mark.asyncio
async def test_reconcile_without_a_routability_check_moves_and_says_unknown(tmp_path):
    """No check supplied means routability is UNKNOWN; that is reported, but
    a dead LiteLLM port is no better, so the device still moves."""
    agent = _agent_with_key(tmp_path, "naira")
    fake = FakeIncus({"taos-agent-naira": PROJECT},
                     {"taos-agent-naira": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    report = await _reconcile(fake, [agent], tmp_path, models_problem=None)
    assert fake.connect["taos-agent-naira"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"
    assert "routability of its models is unknown" in report["repointed"][0]["reason"]


# ---------------------------------------------------------------------------
# Robustness: one bad container never abandons the rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "crash"])
async def test_one_hung_container_does_not_abandon_the_rest(tmp_path, failure):
    a = _agent_with_key(tmp_path, "aaa")
    b = _agent_with_key(tmp_path, "bbb")
    fake = FakeIncus({"taos-agent-aaa": PROJECT, "taos-agent-bbb": PROJECT},
                     {"taos-agent-aaa": f"tcp:127.0.0.1:{LITELLM_PORT}",
                      "taos-agent-bbb": f"tcp:127.0.0.1:{LITELLM_PORT}"})
    calls = {"n": 0}

    async def first_one_explodes(models):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("routing table exploded")
        return None

    if failure == "timeout":
        fake.hang_get.add("taos-agent-aaa")
        report = await _reconcile(fake, [a, b], tmp_path)
        assert [r["agent"] for r in report["repointed"]] == ["bbb"]
        assert [r["agent"] for r in report["skipped"]] == ["aaa"]
    else:
        # A crashing routability check is reported on the agent, never fatal.
        report = await _reconcile(fake, [a, b], tmp_path, models_problem=first_one_explodes)
        moved = {r["agent"]: r.get("reason") for r in report["repointed"]}
        assert set(moved) == {"aaa", "bbb"}
        assert "routability check failed: RuntimeError" in moved["aaa"]
    assert fake.connect["taos-agent-bbb"] == f"tcp:127.0.0.1:{GATEWAY_PORT}"


def test_listener_port_is_7838_and_reserved():
    """7837 belongs to the MLX backend (#3237 / #329); the listener is 7838."""
    from tinyagentos import llm_gateway
    from tinyagentos.installers.port_allocator import RESERVED_PORTS

    assert llm_gateway.DEFAULT_AGENT_PORT == 7838
    assert llm_gateway.agent_port(None) == 7838
    assert 7838 in RESERVED_PORTS


def test_ollama_providers_defined_once():
    import tinyagentos.llm_gateway.forward as forward
    import tinyagentos.llm_gateway.router as router

    assert router.OLLAMA_PROVIDERS is forward.OLLAMA_PROVIDERS
    src = Path(router.__file__).read_text()
    assert "OLLAMA_PROVIDERS = " not in src


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "0"], ids=["flag-unset", "old-off-flag"])
async def test_settings_reports_the_gateway_as_the_llm_proxy(tmp_data_dir, monkeypatch, flag):
    from tinyagentos.app import create_app

    if flag is None:
        monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    else:
        monkeypatch.setenv("TAOS_LLM_GATEWAY", flag)
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    app.state.llm_gateway_agent_port = GATEWAY_PORT
    app.state.llm_gateway_listener_ready = True
    app.state.llm_proxy.port = LITELLM_PORT
    token = app.state.auth.get_local_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        data = (await c.get("/api/settings/llm-proxy")).json()
    assert data["mode"] == "gateway"
    assert data["port"] == GATEWAY_PORT
    assert data["running"] is True and data["url"] == "/api/llm/v1"
    assert "litellm" not in data


# ---------------------------------------------------------------------------
# Backend parity: rkllama and hailo-ollama both expose /v1/chat/completions
# at their pinned refs (rkllama dadea413 measured live; hailo-ollama 1a3ba6be
# read from source: non-stream OpenAI JSON, stream NDJSON the gateway
# translates), and deepseek is OpenAI-compatible.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("backend,model", [
    ({"name": "npu", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}, "default"),
    ({"name": "hailo", "type": "hailo-ollama", "url": "http://localhost:8000", "priority": 1}, "default"),
    ({"name": "ds", "type": "deepseek", "url": "https://api.deepseek.com",
      "models": [{"id": "deepseek-chat"}], "api_key": "sk-ds", "priority": 1}, "deepseek-chat"),
    ({"name": "ds", "type": "deepseek", "url": "",
      "models": [{"id": "deepseek-chat"}], "api_key": "sk-ds", "priority": 1}, "deepseek-chat"),
], ids=["rkllama", "hailo-ollama", "deepseek", "deepseek-default-base"])
async def test_models_problem_serves_npu_and_deepseek_backends(tmp_path, backend, model):
    from tinyagentos.llm_gateway.cutover import models_problem

    state = _state(tmp_path, [])
    state.config.backends = [backend]
    assert await models_problem(state, [model]) is None
