import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tinyagentos.projects.invite_store import ProjectInviteStore
from tinyagentos.routes.project_invites import build_connection_bundle


async def _create_project(client, slug="cloud-proj") -> str:
    resp = await client.post("/api/projects", json={"name": "Cloud Proj", "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _setup_agent_ecosystem(app, monkeypatch, tmp_path):
    from tinyagentos.agent_registry_store import (
        AgentRegistryStore,
        load_or_create_signing_keypair,
    )
    from tinyagentos.auth_requests_store import AuthRequestsStore
    from tinyagentos.agent_grants_store import AgentGrantsStore

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    auth_store = AuthRequestsStore(tmp_path / "auth.db")
    await auth_store.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "auth_requests", auth_store)
    monkeypatch.setattr(app.state, "agent_grants", grants)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))
    return registry, auth_store, grants


async def _mint_invite(client, pid, *, kind="agent", scopes=None, approval_mode="auto", check_interval_secs=None, ttl_secs=None):
    payload = {
        "scopes": scopes or ["a2a_send"],
        "approval_mode": approval_mode,
    }
    if kind != "agent":
        payload["kind"] = kind
    if check_interval_secs is not None:
        payload["check_interval_secs"] = check_interval_secs
    if ttl_secs is not None:
        payload["ttl_secs"] = ttl_secs
    resp = await client.post(f"/api/projects/{pid}/invites", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["invite_id"], data["pin"]


class TestCloudAgentInviteKind:
    """Test that 'cloud_agent' is a valid invite kind in the store."""

    @pytest.mark.asyncio
    async def test_cloud_agent_kind_accepted_in_store(self, tmp_path):
        store = ProjectInviteStore(tmp_path / "invites.db")
        await store.init()
        result = await store.mint(
            project_id="prj_test",
            scopes=["a2a_send"],
            approval_mode="auto",
            check_interval_secs=900,
            created_by="admin",
            kind="cloud_agent",
        )
        assert result["record"]["kind"] == "cloud_agent"
        assert result["record"]["check_interval_secs"] == 900

    @pytest.mark.asyncio
    async def test_unknown_kind_rejected_in_store(self, tmp_path):
        store = ProjectInviteStore(tmp_path / "invites.db")
        await store.init()
        with pytest.raises(ValueError, match="invalid invite kind"):
            await store.mint(
                project_id="prj_test",
                scopes=["a2a_send"],
                approval_mode="auto",
                check_interval_secs=900,
                created_by="admin",
                kind="unknown_kind",
            )


class TestMintCloudAgentInvite:
    """Test mint_invite endpoint with kind=cloud_agent."""

    @pytest.mark.asyncio
    async def test_mint_cloud_agent_no_relay_env_returns_400(self, client, app, monkeypatch):
        """Minting cloud_agent without TAOS_CONTROLLER_RELAY_URL must return 400."""
        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": ["a2a_send"], "approval_mode": "auto", "kind": "cloud_agent"},
        )
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert "not reachable from the internet" in data["error"]
        assert "taOSgo" in data["error"]

    @pytest.mark.asyncio
    async def test_mint_cloud_agent_http_relay_returns_400(self, client, app, monkeypatch):
        """Minting cloud_agent with http:// relay must return 400."""
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "http://relay.example.test")
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": ["a2a_send"], "approval_mode": "auto", "kind": "cloud_agent"},
        )
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert "not reachable from the internet" in data["error"]

    @pytest.mark.asyncio
    async def test_mint_cloud_agent_with_https_relay_succeeds(self, client, app, monkeypatch):
        """Minting cloud_agent with valid https:// relay must succeed."""
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": ["a2a_send"], "approval_mode": "auto", "kind": "cloud_agent"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["invite_id"]) == 6
        assert len(data["pin"]) == 4
        # project_tasks is auto-added for project-scoped invites
        assert "project_tasks" in data["scopes"]
        assert "a2a_send" in data["scopes"]

    @pytest.mark.asyncio
    async def test_mint_cloud_agent_defaults_check_interval_to_900(self, client, app, monkeypatch):
        """cloud_agent invite defaults check_interval_secs to 900 when omitted."""
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": ["a2a_send"], "approval_mode": "auto", "kind": "cloud_agent"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["check_interval_secs"] == 900

    @pytest.mark.asyncio
    async def test_mint_cloud_agent_respects_explicit_check_interval(self, client, app, monkeypatch):
        """Explicit check_interval_secs is respected for cloud_agent."""
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={
                "scopes": ["a2a_send"],
                "approval_mode": "auto",
                "kind": "cloud_agent",
                "check_interval_secs": 300,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["check_interval_secs"] == 300

    @pytest.mark.asyncio
    async def test_mint_cloud_agent_explicit_1800_stays_1800(self, client, app, monkeypatch):
        """Explicit check_interval_secs=1800 for cloud_agent must not be rewritten to 900."""
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={
                "scopes": ["a2a_send"],
                "approval_mode": "auto",
                "kind": "cloud_agent",
                "check_interval_secs": 1800,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["check_interval_secs"] == 1800

    @pytest.mark.asyncio
    async def test_mint_unknown_kind_returns_400(self, client, app):
        """Unknown kind must be rejected with 400."""
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": ["a2a_send"], "approval_mode": "auto", "kind": "not_a_kind"},
        )
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert "invalid invite kind" in data["error"]


class TestRedeemCloudAgentInvite:
    """Test redeem flow for cloud_agent kind."""

    @pytest.mark.asyncio
    async def test_redeem_cloud_agent_bundle_only_relay_endpoints(self, client, app, monkeypatch, tmp_path):
        """Redeemed cloud_agent bundle must contain ONLY relay endpoints, no LAN/mesh."""
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")

        pid = await _create_project(client, slug="cloudproj")
        iid, pin = await _mint_invite(client, pid, kind="cloud_agent", scopes=["a2a_send"])

        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "claude"},
        )
        assert resp.status_code == 200, resp.text
        bundle = resp.json()["bundle"]

        endpoints = bundle["controller"]["endpoints"]
        # Must have exactly one endpoint: the relay
        assert len(endpoints) == 1
        assert endpoints[0]["kind"] == "relay"
        assert endpoints[0]["url"] == "https://relay.example.test"
        assert endpoints[0]["priority"] == 1

        # No endpoint URL must contain ':7900' (raw bus port)
        for ep in endpoints:
            assert ":7900" not in ep["url"]

        # No LAN or mesh endpoints
        for ep in endpoints:
            assert ep["kind"] != "lan"
            assert ep["kind"] != "mesh"

    @pytest.mark.asyncio
    async def test_redeem_cloud_agent_no_token_in_bundle(self, client, app, monkeypatch, tmp_path):
        """Token must stay OUT of the bundle for cloud_agent (same as today)."""
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")

        pid = await _create_project(client, slug="cloudproj2")
        iid, pin = await _mint_invite(client, pid, kind="cloud_agent", scopes=["a2a_send"])

        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "claude"},
        )
        assert resp.status_code == 200, resp.text
        bundle = resp.json()["bundle"]

        assert "token" not in bundle
        assert "secret" not in bundle
        blob = json.dumps(bundle)
        assert ".eyJ" not in blob  # no JWT in bundle


class TestDefaultAgentKindUnchanged:
    """Test that omitting kind (default 'agent') preserves existing behaviour."""

    @pytest.mark.asyncio
    async def test_mint_default_kind_agent_unchanged(self, client, app, monkeypatch):
        """Default kind='agent' must work without relay and include LAN endpoints."""
        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        pid = await _create_project(client)

        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": ["a2a_send"], "approval_mode": "auto"},  # no kind field
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["check_interval_secs"] == 1800  # default for agent

    @pytest.mark.asyncio
    async def test_redeem_default_kind_includes_lan_endpoints(self, client, app, monkeypatch, tmp_path):
        """Default kind='agent' redeem bundle must include LAN endpoints."""
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)

        pid = await _create_project(client, slug="defaultproj")
        iid, pin = await _mint_invite(client, pid)  # no kind = default 'agent'

        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "claude"},
        )
        assert resp.status_code == 200, resp.text
        bundle = resp.json()["bundle"]

        endpoints = bundle["controller"]["endpoints"]
        # Should have at least one LAN endpoint (mocked)
        lan_eps = [ep for ep in endpoints if ep["kind"] == "lan"]
        assert len(lan_eps) >= 1


class TestBuildConnectionBundleCloudAgent:
    """Test build_connection_bundle filtering for cloud_agent kind."""

    @pytest.mark.asyncio
    async def test_build_connection_bundle_cloud_agent_only_relay(self, monkeypatch):
        """build_connection_bundle with cloud_agent kind must filter to relay only."""
        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
        monkeypatch.delenv("TAOS_CONTROLLER_CALLBACK_HOST", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        invite_record = {
            "invite_id": "123456",
            "kind": "cloud_agent",
            "check_interval_secs": 900,
        }
        project = {"id": "prj_test", "name": "Test", "slug": "test"}

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips",
            return_value=["192.168.1.1"],
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": True, "node_ip": "100.64.0.7"},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value=None,
        ):
            bundle = await build_connection_bundle(
                req,
                invite_record=invite_record,
                project=project,
                agent_handle="test-claude",
                granted_scopes=["project_tasks", "a2a_send"],
                check_interval_secs=900,
                harness="claude",
            )

        endpoints = bundle["controller"]["endpoints"]
        # Must have exactly one endpoint: the relay
        assert len(endpoints) == 1
        assert endpoints[0]["kind"] == "relay"
        assert endpoints[0]["url"] == "https://relay.example.test"

        # No LAN or mesh endpoints
        for ep in endpoints:
            assert ep["kind"] != "lan"
            assert ep["kind"] != "mesh"

        # No endpoint URL must contain ':7900'
        for ep in endpoints:
            assert ":7900" not in ep["url"]

    @pytest.mark.asyncio
    async def test_build_connection_bundle_agent_kind_unchanged(self, monkeypatch):
        """build_connection_bundle with default agent kind must include all endpoints."""
        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.delenv("TAOS_CONTROLLER_CALLBACK_HOST", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        invite_record = {
            "invite_id": "123456",
            "kind": "agent",
            "check_interval_secs": 1800,
        }
        project = {"id": "prj_test", "name": "Test", "slug": "test"}

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips",
            return_value=["192.168.1.1"],
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": True, "node_ip": "100.64.0.7"},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value=None,
        ):
            bundle = await build_connection_bundle(
                req,
                invite_record=invite_record,
                project=project,
                agent_handle="test-claude",
                granted_scopes=["project_tasks", "a2a_send"],
                check_interval_secs=1800,
                harness="claude",
            )

        endpoints = bundle["controller"]["endpoints"]
        # Should have LAN and mesh endpoints
        kinds = {ep["kind"] for ep in endpoints}
        assert "lan" in kinds
        assert "mesh" in kinds


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
