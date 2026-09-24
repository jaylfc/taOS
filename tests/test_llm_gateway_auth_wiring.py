"""G2 wiring: node/agent revocation cuts gateway access, and the middleware
exempts exactly the two gateway method+path pairs.

All through the real app (create_app on a tmp data dir, gateway flag on) and
the real gateway routes.
"""
from __future__ import annotations

import json as _json

import pytest
import pytest_asyncio

import tinyagentos.llm_gateway.auth as gw
from tinyagentos.auth_middleware import _is_exempt
from tinyagentos.config import load_config

from test_llm_gateway_auth import (  # noqa: F401
    _ASYNC,
    CHAT,
    MODELS,
    _assert_openai_401,
    app,
    bare,
    client,
    gateway_client,
)
from test_routes_cluster_pairing import pair_worker, sign_worker_request


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def _wiring_stores(gateway_client):
    """The extra stores the cluster and archive routes touch, initialised ONCE
    on G1's module app (never a per-test create_app)."""
    state = gateway_client._transport.app.state
    stores = [state.notifications, state.cluster_pairing,
              state.cluster_manager._registry_store]
    for store in stores:
        if getattr(store, "_db", None) is not None:
            await store.close()
        await store.init()
    yield
    for store in stores:
        await store.close()


@pytest.fixture(autouse=True)
def _reset_cluster_and_agents(app):
    """Put back what these tests change: the worker list and the agent list."""
    import copy
    cluster = app.state.cluster_manager
    config = app.state.config
    agents = copy.deepcopy(config.agents)
    archived = copy.deepcopy(getattr(config, "archived_agents", []))
    workers = dict(cluster._workers)
    yield
    config.agents = agents
    config.archived_agents = archived
    cluster._workers.clear()
    cluster._workers.update(workers)


def _h(key):
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------------------
# Node revocation cuts model access in the same step
# ---------------------------------------------------------------------------


@_ASYNC
class TestNodeRevocationCutsModelAccess:
    async def _paired_node_with_key(self, client, app, bare, name, ip):
        await pair_worker(client, app, name, f"http://{ip}:9000")
        key = gw.mint_for_node(name, ["gpt-small"], data_dir=app.state.data_dir)
        assert (await bare.get(MODELS, headers=_h(key))).status_code == 200
        return key

    async def test_revoke_node_kills_its_key(self, client, app, bare):
        key = await self._paired_node_with_key(client, app, bare, "gw-rev", "10.9.0.1")
        resp = await client.post("/api/cluster/workers/gw-rev/revoke")
        assert resp.status_code == 200
        _assert_openai_401(await bare.get(MODELS, headers=_h(key)))

    async def test_revoke_node_leaves_other_nodes_alone(self, client, app, bare):
        mine = await self._paired_node_with_key(client, app, bare, "gw-a", "10.9.0.2")
        theirs = gw.mint_for_node("gw-b", ["gpt-small"], data_dir=app.state.data_dir)
        assert (await client.post("/api/cluster/workers/gw-a/revoke")).status_code == 200
        _assert_openai_401(await bare.get(MODELS, headers=_h(mine)))
        assert (await bare.get(MODELS, headers=_h(theirs))).status_code == 200

    async def test_block_node_kills_its_key(self, client, app, bare):
        key = await self._paired_node_with_key(client, app, bare, "gw-blk", "10.9.0.3")
        resp = await client.post("/api/cluster/workers/gw-blk/block")
        assert resp.status_code == 200
        _assert_openai_401(await bare.get(MODELS, headers=_h(key)))

    async def test_old_key_stays_dead_after_unblock(self, client, app, bare):
        key = await self._paired_node_with_key(client, app, bare, "gw-unb", "10.9.0.4")
        assert (await client.post("/api/cluster/workers/gw-unb/block")).status_code == 200
        assert (await client.post("/api/cluster/workers/gw-unb/unblock")).status_code == 200
        _assert_openai_401(await bare.get(MODELS, headers=_h(key)))

    async def test_delete_worker_kills_its_key(self, client, app, bare):
        sk = await pair_worker(client, app, "gw-del", "http://10.9.0.5:9000")
        body = _json.dumps({"name": "gw-del", "url": "http://10.9.0.5:9000", "platform": "linux"}).encode()
        headers = sign_worker_request(sk, "gw-del", "POST", "/api/cluster/workers", body)
        resp = await client.post(
            "/api/cluster/workers", content=body,
            headers={**headers, "content-type": "application/json"},
        )
        assert resp.status_code == 200, resp.text
        key = gw.mint_for_node("gw-del", ["gpt-small"], data_dir=app.state.data_dir)
        assert (await bare.get(MODELS, headers=_h(key))).status_code == 200
        resp = await client.delete("/api/cluster/workers/gw-del")
        assert resp.status_code == 200, resp.text
        _assert_openai_401(await bare.get(MODELS, headers=_h(key)))

    async def test_refused_revoke_does_not_revoke(self, client, app, bare):
        """A revoke the route refuses (unknown node -> 404) must not cut keys:
        the revocation rides on the node revocation, not on the request."""
        key = gw.mint_for_node("never-paired", ["gpt-small"], data_dir=app.state.data_dir)
        resp = await client.post("/api/cluster/workers/never-paired/revoke")
        assert resp.status_code == 404
        assert (await bare.get(MODELS, headers=_h(key))).status_code == 200


# ---------------------------------------------------------------------------
# Agent archive (DELETE /api/agents/{name}) cuts model access
# ---------------------------------------------------------------------------


@_ASYNC
class TestAgentArchiveCutsModelAccess:
    async def test_archive_with_container_revokes(self, client, app, bare, monkeypatch):
        async def _exists(name):
            return True

        async def _ok(*a, **k):
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.container_exists", _exists)
        monkeypatch.setattr("tinyagentos.containers.stop_container", _ok)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", _ok)
        key = gw.mint_gateway_key(
            bound_to="test-agent", kind="agent", allowed_models=["gpt-small"],
            data_dir=app.state.data_dir,
        )
        assert (await bare.get(MODELS, headers=_h(key))).status_code == 200
        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200, resp.text
        assert load_config(app.state.data_dir / "config.yaml").archived_agents
        _assert_openai_401(await bare.get(MODELS, headers=_h(key)))

    async def test_orphan_hard_delete_revokes(self, client, app, bare, monkeypatch):
        async def _no(name, *a, **k):
            return False

        async def _none(*a, **k):
            return None

        monkeypatch.setattr("tinyagentos.containers.container_exists", _no)
        monkeypatch.setattr("tinyagentos.containers.resolve_agent_container", _none)
        key = gw.mint_gateway_key(
            bound_to="test-agent", kind="agent", allowed_models=["gpt-small"],
            data_dir=app.state.data_dir,
        )
        resp = await client.delete("/api/agents/test-agent")
        assert resp.status_code == 200, resp.text
        _assert_openai_401(await bare.get(MODELS, headers=_h(key)))

    async def test_unknown_agent_revokes_nothing(self, client, app, bare):
        key = gw.mint_gateway_key(
            bound_to="test-agent", kind="agent", allowed_models=["gpt-small"],
            data_dir=app.state.data_dir,
        )
        resp = await client.delete("/api/agents/no-such-agent")
        assert resp.status_code == 404
        assert (await bare.get(MODELS, headers=_h(key))).status_code == 200


# ---------------------------------------------------------------------------
# Middleware exemption: exactly two method+path pairs
# ---------------------------------------------------------------------------


class TestExemptionUnit:
    def test_exact_pairs_exempt(self):
        assert _is_exempt("POST", CHAT)
        assert _is_exempt("GET", MODELS)

    @pytest.mark.parametrize("method,path", [
        ("GET", CHAT),
        ("POST", MODELS),
        ("PUT", CHAT),
        ("DELETE", MODELS),
        ("GET", "/api/llm/v1/other"),
        ("POST", "/api/llm/v1/other"),
        ("GET", "/api/llm/v2/models"),
        ("POST", "/api/llm/v2/chat/completions"),
        ("GET", "/api/llm/v1/models/"),
        ("GET", "/api/llm/v1/models/gpt-a"),
        ("POST", "/api/llm/v1/chat/completions/"),
        ("POST", "/api/llm/v1/chat/completions/x"),
        ("POST", "/api/llm/v1/completions"),
        ("POST", "/api/llm/v1/embeddings"),
        ("GET", "/api/llm/v1"),
        ("GET", "/api/llm/v1/"),
        ("GET", "/api/llm/v1/Models"),
        ("GET", "/api/llm/v1//models"),
        ("GET", "/api/llm/v1/../v1/models"),
    ])
    def test_neighbours_not_exempt(self, method, path):
        assert not _is_exempt(method, path)


@_ASYNC
class TestExemptionThroughRealMiddleware:
    async def test_exempt_pairs_reach_the_route(self, bare, app):
        """With a valid gateway key and no session, the two pairs get past the
        middleware and the route answers."""
        key = gw.mint_gateway_key(
            bound_to="mw-agent", kind="agent", allowed_models=["gpt-small"],
            data_dir=app.state.data_dir,
        )
        resp = await bare.get(MODELS, headers=_h(key))
        assert resp.status_code == 200, resp.text
        # Reaching the route: a body the route rejects (400), not the gate (401).
        resp = await bare.post(CHAT, json={"model": "gpt-small"}, headers=_h(key))
        assert resp.status_code == 400, resp.text

    async def test_exempt_pairs_without_key_are_401(self, bare):
        for resp in (await bare.get(MODELS), await bare.post(CHAT, json={})):
            _assert_openai_401(resp)

    @pytest.mark.parametrize("method,path", [
        ("GET", CHAT),
        ("POST", MODELS),
        ("GET", "/api/llm/v1/other"),
        ("POST", "/api/llm/v1/other"),
        ("GET", "/api/llm/v2/models"),
        ("POST", "/api/llm/v2/chat/completions"),
        ("GET", "/api/llm/v1/models/x"),
        ("POST", "/api/llm/v1/embeddings"),
    ])
    async def test_neighbours_stay_gated_with_a_gateway_key(self, bare, app, method, path):
        """A neighbour that got past the middleware would answer 404/405; the
        gate answers 401 (OpenAI-shaped on /api/llm/*), with or without a key."""
        key = gw.mint_gateway_key(
            bound_to="mw-agent2", kind="agent", allowed_models=["gpt-small"],
            data_dir=app.state.data_dir,
        )
        for headers in ({}, _h(key)):
            resp = await bare.request(method, path, headers=headers, json={"model": "gpt-small"})
            _assert_openai_401(resp)
