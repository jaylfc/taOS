"""Tests for POST /api/containers/requests (P1 container provisioning).

Covers the policy evaluation: under quota auto-approves, over quota lands in
pending-approval, over threshold escalates to a Decisions-app item. Includes
policy-mutation tests that prove the policy is the actual gate (mutating the
quota changes whether an over-quota request is auto-approved).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.container_requests_store import ContainerRequestStore
from tinyagentos.containers.provisioning_policy import (
    PolicyConfig,
    ProvisioningPolicy,
)
from taos_test_csrf import csrf_event_hooks


class _Env:
    """Fresh stores + a registered active agent, wired onto app.state."""

    def __init__(
        self,
        registry,
        grants,
        request_store,
        decision_store,
        priv,
        pub,
        admin_uid,
    ):
        self.registry = registry
        self.grants = grants
        self.request_store = request_store
        self.decision_store = decision_store
        self.priv = priv
        self.pub = pub
        self.admin_uid = admin_uid

    def agent_token(self, canonical_id: str, framework: str = "claude") -> str:
        return mint_registry_token(
            canonical_id, self.priv, user_id=self.admin_uid, framework=framework
        )

    async def close(self):
        await self.registry.close()
        await self.grants.close()
        await self.request_store.close()
        await self.decision_store.close()


async def _wire(client, monkeypatch, tmp_path, *, owner_uid=None, policy=None):
    """Build fresh stores, register one ACTIVE agent, monkeypatch app.state."""
    app = client._transport.app
    admin = app.state.auth.find_user("admin")
    admin_uid = admin["id"] if admin else ""
    owner_uid = owner_uid if owner_uid is not None else admin_uid

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    request_store = ContainerRequestStore(tmp_path / "cr.db")
    await request_store.init()
    decision_store = app.state.decision_store
    if decision_store._db is not None:
        await decision_store.close()
    await decision_store.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "agent_grants", grants)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))
    monkeypatch.setattr(app.state, "container_request_store", request_store)
    monkeypatch.setattr(app.state, "decision_store", decision_store)

    if policy is not None:
        monkeypatch.setattr(app.state, "provisioning_policy", policy)
    else:
        monkeypatch.setattr(
            app.state,
            "provisioning_policy",
            ProvisioningPolicy(PolicyConfig(quota=1, threshold=3, per_agent_quota={}, per_agent_threshold={})),
        )

    env = _Env(registry, grants, request_store, decision_store, priv, pub, admin_uid)
    env.owner_uid = owner_uid
    return env


async def _register_active(env, *, handle="@worker", display="worker", framework="claude"):
    rec = await env.registry.register(
        framework=framework,
        display_name=display,
        user_id=env.owner_uid,
        origin="taos-deployed",
        handle=handle,
    )
    return rec["canonical_id"]


def _app(client):
    return client._transport.app


# ---------------------------------------------------------------------------
# Basic auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_token_returns_401(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        resp = await client.post(
            "/api/containers/requests",
            json={"image": "images:debian/bookworm"},
        )
        assert resp.status_code == 401, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_invalid_token_returns_401(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        app = _app(client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": "Bearer garbage"},
                json={"image": "images:debian/bookworm"},
            )
        assert resp.status_code == 401, resp.text
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Policy: under quota auto-approves
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_under_quota_auto_approved(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)
        app = _app(client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "first"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved", body
        assert body["canonical_id"] == cid
        assert "request_id" in body
        assert "decision_id" not in body

        stored = await env.request_store.get(body["request_id"])
        assert stored["status"] == "approved"
        assert stored["canonical_id"] == cid
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Policy: over quota -> pending-approval (NOT auto-approved)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_over_quota_not_auto_approved(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)

        # First request: under quota (0 active) -> approved.
        token = env.agent_token(cid)
        app = _app(client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp1 = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "first"},
            )
        assert resp1.status_code == 200, resp1.text
        assert resp1.json()["status"] == "approved"

        # Second request: now 1 active, quota=1 -> NOT approved.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp2 = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "second"},
            )
        assert resp2.status_code == 200, resp2.text
        body2 = resp2.json()
        assert body2["status"] == "pending-approval", body2
        assert "decision_id" not in body2  # not escalated, just pending

        stored2 = await env.request_store.get(body2["request_id"])
        assert stored2["status"] == "pending-approval"
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Policy: mutate quota and prove the gate flips
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_policy_mutation_changes_approval(client, monkeypatch, tmp_path):
    """With quota=5 the same over-quota (was 1) scenario auto-approves.
    Mutating the policy makes an over-quota request flip from not-approved
    to approved, proving the policy is the actual gate."""
    app = _app(client)
    env = await _wire(
        client,
        monkeypatch,
        tmp_path,
        policy=ProvisioningPolicy(PolicyConfig(quota=5, threshold=10)),
    )
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)

        # Create ONE request: 0 active, under quota=5 -> approved.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp1 = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "first"},
            )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "approved"

        # Second request: 1 active, still under quota=5 -> approved.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp2 = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "second"},
            )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["status"] == "approved", resp2.json()
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Policy: over threshold -> escalated to a Decision
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_over_threshold_escalates_to_decision(client, monkeypatch, tmp_path):
    """With quota=1, threshold=3: fill to 3 active, then the 4th escalates."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)
        app = _app(client)

        # Pre-fill 3 requests so the agent is AT the threshold (count == 3).
        for _ in range(3):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as bare:
                r = await bare.post(
                    "/api/containers/requests",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"image": "images:debian/bookworm"},
                )
            assert r.status_code == 200, r.text

        # 4th request: count=3 >= threshold=3 -> escalate.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "escalated"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "pending-approval", body
        assert "decision_id" in body, body

        # The Decision must exist and reference the request in its metadata.
        decision = await env.decision_store.get(body["decision_id"])
        assert decision is not None
        assert decision["from_agent"] == cid
        assert decision["type"] == "approve_deny"
        assert decision["status"] == "pending"
        meta = decision["metadata"]
        assert meta["request_id"] == body["request_id"]
        assert meta["canonical_id"] == cid

        # The request record must be linked to the decision.
        stored = await env.request_store.get(body["request_id"])
        assert stored["decision_id"] == body["decision_id"]
        assert stored["status"] == "pending-approval"
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Policy: per-agent override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_agent_quota_override(client, monkeypatch, tmp_path):
    """A per-agent override of quota takes precedence over the default."""
    app = _app(client)
    env = await _wire(
        client,
        monkeypatch,
        tmp_path,
        policy=ProvisioningPolicy(
            PolicyConfig(quota=1, threshold=5, per_agent_quota={"agent-xyz": 3})
        ),
    )
    try:
        # Register with handle "xyz" -> canonical_id will be a generated "ag-..." id,
        # not "agent-xyz". We need to inject the override for the ACTUAL canonical_id.
        cid = await _register_active(env, handle="@xyz", display="xyz")
        # Override the per-agent quota for the real canonical_id.
        env_policy = app.state.provisioning_policy
        env_policy.configure(
            PolicyConfig(quota=1, threshold=5, per_agent_quota={cid: 3, "agent-xyz": 3})
        )

        token = env.agent_token(cid)

        # 3 requests under quota=3 -> first 3 approved, 4th pending.
        statuses = []
        for i in range(4):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as bare:
                r = await bare.post(
                    "/api/containers/requests",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"image": "images:debian/bookworm", "reason": f"req-{i}"},
                )
            assert r.status_code == 200, r.text
            statuses.append(r.json()["status"])

        assert statuses[:3] == ["approved", "approved", "approved"], statuses
        assert statuses[3] == "pending-approval", statuses
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Policy: threshold below quota is clamped to quota
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_threshold_clamped_to_quota(client, monkeypatch, tmp_path):
    """If threshold < quota, the policy clamps threshold to quota so it never
    escalates before the quota is even reached."""
    env = await _wire(
        client,
        monkeypatch,
        tmp_path,
        policy=ProvisioningPolicy(PolicyConfig(quota=5, threshold=2)),
    )
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)
        app = _app(client)

        # 5 requests: all should be approved (threshold clamped to 5).
        statuses = []
        for _ in range(5):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as bare:
                r = await bare.post(
                    "/api/containers/requests",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"image": "images:debian/bookworm"},
                )
            assert r.status_code == 200, r.text
            statuses.append(r.json()["status"])
        assert all(s == "approved" for s in statuses), statuses

        # 6th request: 5 active, >= quota (5), >= threshold (5) -> escalate.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            r = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "sixth"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "pending-approval"
        assert "decision_id" in body, body
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Default config (no explicit policy monkeypatched)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_config_aut_approve_and_escalate(client, monkeypatch, tmp_path):
    """With the DEFAULT policy (quota=2, threshold=5), the first 2 are approved,
    3rd and 4th are pending-approval, and the 5th escalates."""
    app = _app(client)
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        # Restore the default policy from config (the _wire helper sets quota=1).
        monkeypatch.setattr(
            app.state,
            "provisioning_policy",
            ProvisioningPolicy(PolicyConfig(quota=2, threshold=5)),
        )
        cid = await _register_active(env)
        token = env.agent_token(cid)

        statuses = []
        decision_ids = []
        for i in range(5):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as bare:
                r = await bare.post(
                    "/api/containers/requests",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"image": "images:debian/bookworm", "reason": f"req-{i}"},
                )
            assert r.status_code == 200, r.text
            body = r.json()
            statuses.append(body["status"])
            decision_ids.append(body.get("decision_id"))

        # 0 active -> approved; 1 active -> approved; 2 active -> pending.
        assert statuses[:2] == ["approved", "approved"], statuses
        # 3rd and 4th are pending-approval (over quota, under threshold).
        assert statuses[2:4] == ["pending-approval", "pending-approval"], statuses
        assert decision_ids[2:4] == [None, None], decision_ids
        # 5th: count=4, < threshold=5, so still pending-approval, NOT escalated.
        assert statuses[4] == "pending-approval", statuses
        assert decision_ids[4] is None, decision_ids

        # 6th: count=5 >= threshold=5 -> escalate.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            r = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm", "reason": "sixth"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "pending-approval"
        assert "decision_id" in body, body

        decision = await env.decision_store.get(body["decision_id"])
        assert decision is not None
        assert decision["from_agent"] == cid
        assert decision["type"] == "approve_deny"
        meta = decision["metadata"]
        assert meta["request_id"] == body["request_id"]
        assert meta["canonical_id"] == cid
        assert meta["active_count"] == 5
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Failed terminal state does NOT count toward quota
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_request_does_not_count_toward_quota(client, monkeypatch, tmp_path):
    """A request in the 'failed' state is terminal and does not consume quota,
    so the next request under a tight quota is auto-approved."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)
        app = _app(client)

        # First request: approved (under quota=1).
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            r = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm"},
            )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        # Mark that request as failed (simulating a P2 provisioning failure).
        crq_id = r.json()["request_id"]
        await env.request_store.set_status(crq_id, "failed")

        # Second request: active_count is now 0 (failed doesn't count) -> approved.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            r2 = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "images:debian/bookworm"},
            )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "approved", r2.json()
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Identity is taken from the token, not the body
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canonical_id_from_token_not_body(client, monkeypatch, tmp_path):
    """The canonical_id is always derived from the verified token. A request
    body cannot bill another agent's quota."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)
        app = _app(client)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                "/api/containers/requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"image": "img", "canonical_id": "agent-evil", "reason": "x"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["canonical_id"] == cid
        assert body["status"] == "approved"
    finally:
        await env.close()
