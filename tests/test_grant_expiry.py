"""Tests for the approve-path grant expiry: duration_secs -> expires_at.

Two layers are asserted here, because the wiring is what #2985 was actually
missing:

* ``TestExpiresAtFromDuration`` — the pure mapping (positive int -> future
  timezone-aware ISO, anything else -> None), unit-level.
* ``TestApprovePathWiresExpiry`` — the *route-level* mapping: a pending request
  carrying ``duration_secs`` must end up with that expiry persisted on the
  granted scope after approval, and a request without it must stay unbounded.
  These drive the real approve machinery (``approve_request_record`` via the
  HTTP route) against a real ``AgentGrantsStore``, so they fail if the
  ``expires_at=expires_at`` argument is dropped from either ``add_grant`` call
  site. The store-level persistence itself is covered by
  ``tests/test_agent_grants_store.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tinyagentos.routes.agent_auth_requests import _expires_at_from_duration


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class TestExpiresAtFromDuration:
    def test_positive_duration_yields_future_expiry(self):
        now = datetime.now(timezone.utc)
        expires = _expires_at_from_duration(3600)
        assert expires is not None
        parsed = _parse(expires)
        assert parsed.tzinfo is not None, "expiry must be timezone-aware"
        delta = parsed - now
        # allow a small clock skew window around the intended 1 hour
        assert timedelta(minutes=59) < delta <= timedelta(hours=1, minutes=1)

    def test_none_duration_is_unbounded(self):
        assert _expires_at_from_duration(None) is None

    def test_zero_duration_is_unbounded(self):
        assert _expires_at_from_duration(0) is None

    def test_negative_duration_is_unbounded(self):
        assert _expires_at_from_duration(-60) is None

    def test_non_int_duration_is_unbounded(self):
        # a float or string must not produce a bogus expiry
        assert _expires_at_from_duration(3660.5) is None
        assert _expires_at_from_duration("3600") is None

    def test_short_duration_still_in_the_future(self):
        expires = _expires_at_from_duration(30)
        assert expires is not None
        parsed = _parse(expires)
        assert parsed > datetime.now(timezone.utc) - timedelta(seconds=1)


class TestApprovePathWiresExpiry:
    """The route-level mapping #2985 exists to add: approving a request must
    persist its duration_secs as a real future expires_at on the grant. These
    tests drive the HTTP approve route against a real store, so they are the
    red that fails when the `expires_at=` wiring is removed from add_grant."""

    async def _approve(self, client, monkeypatch, tmp_path, duration_secs):
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

        # A global, non-project scope keeps the approve path simple (no
        # project_id binding or membership sync needed).
        rec = await auth_store.create(
            identity_claim="@expiry-agent", framework="openclaw",
            requested_scopes=["registry_feeds_read"], requested_skills=None, reason="",
            duration_secs=duration_secs, project_id=None,
        )
        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))

        resp = await client.post(
            f"/api/agents/auth-requests/{rec['id']}/approve",
            json={"granted_scopes": ["registry_feeds_read"]},
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["canonical_id"]

        agent_grants = await grants.list_grants(cid)
        scoped = [g for g in agent_grants if g["scope"] == "registry_feeds_read"]
        assert len(scoped) == 1, f"expected one registry_feeds_read grant, got {agent_grants}"
        expiry = scoped[0].get("expires_at")

        await registry.close()
        await auth_store.close()
        await grants.close()
        return expiry

    @pytest.mark.asyncio
    async def test_duration_secs_persists_future_expiry_on_grant(
        self, client, monkeypatch, tmp_path
    ):
        expiry = await self._approve(client, monkeypatch, tmp_path, 3600)
        assert expiry is not None, "grant must carry a future expiry when duration_secs was set"
        parsed = _parse(expiry)
        assert parsed.tzinfo is not None, "persisted expiry must be timezone-aware"
        delta = parsed - datetime.now(timezone.utc)
        assert timedelta(minutes=59) < delta <= timedelta(hours=1, minutes=1)

    @pytest.mark.asyncio
    async def test_missing_duration_stays_unbounded(
        self, client, monkeypatch, tmp_path
    ):
        expiry = await self._approve(client, monkeypatch, tmp_path, None)
        assert expiry is None, "grant must stay unbounded when duration_secs is absent"


class TestDeferredPathWiresExpiry:
    """The add_agent_to_project reuse arm must also receive expires_at from
    duration_secs, but that path has no route-level test."""

    async def _approve_via_reuse(self, client, monkeypatch, tmp_path, duration_secs):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from unittest.mock import AsyncMock, MagicMock

        registry = AgentRegistryStore(tmp_path / "reg.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth.db")
        await auth_store.init()
        grants_spy = AsyncMock()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

        existing = await registry.register(
            framework="openclaw",
            display_name="@expiry-agent",
            user_id="test",
            origin="external-selfjoin",
            handle="expiry-agent",
            allow_reserved=True,
        )
        await registry.set_status(existing["canonical_id"], "active", actor="test")

        rec = await auth_store.create(
            identity_claim="@expiry-agent", framework="openclaw",
            requested_scopes=["project_tasks"], requested_skills=None, reason="",
            duration_secs=duration_secs, project_id="proj-1",
        )
        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants_spy)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))

        resp = await client.post(
            f"/api/agents/auth-requests/{rec['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "project_id": "proj-1"},
        )
        assert resp.status_code == 200, resp.text

        add_grant_calls = [
            c for c in grants_spy.add_grant.call_args_list
            if c.args and len(c.args) > 1 and c.args[1] == "project_tasks"
        ]
        assert len(add_grant_calls) == 1, (
            f"expected one project_tasks add_grant call through add_agent_to_project, "
            f"got {add_grant_calls}"
        )
        expires_at = add_grant_calls[0].kwargs.get("expires_at")
        return expires_at

    @pytest.mark.asyncio
    async def test_duration_secs_persists_expiry_via_add_agent_to_project(
        self, client, monkeypatch, tmp_path
    ):
        expiry = await self._approve_via_reuse(client, monkeypatch, tmp_path, 3600)
        assert expiry is not None, (
            "add_agent_to_project must carry expires_at when duration_secs was set"
        )

    @pytest.mark.asyncio
    async def test_missing_duration_stays_unbounded_via_add_agent_to_project(
        self, client, monkeypatch, tmp_path
    ):
        expiry = await self._approve_via_reuse(client, monkeypatch, tmp_path, None)
        assert expiry is None, (
            "add_agent_to_project must receive None expires_at when duration_secs is absent"
        )

