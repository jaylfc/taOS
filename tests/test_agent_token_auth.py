"""Unit tests for the project-scoped registry-JWT check.

``check_agent_scope_for_project`` is the least-privilege sibling of
``check_agent_scope``: it verifies the same EdDSA JWT + active-grant chain AND
binds the caller to a single project via the token's ``project_id`` claim. These
tests pin the security-critical behavior (project binding, missing-claim reject)
and confirm ``check_agent_scope`` is unchanged by the shared-verifier refactor.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.agent_token_auth import (
    check_agent_scope,
    check_agent_scope_for_project,
)


class _FakeRequest:
    """Minimal stand-in for a starlette Request: the auth helpers only touch
    ``.headers.get(...)`` and ``.app.state`` on the object."""

    def __init__(self, app, token: str | None = None):
        self.app = app
        self.headers = {}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"


@pytest_asyncio.fixture
async def token_app(app):
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    yield app
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


async def _mint(app, *, scopes=("project_tasks",), project_id="prj-1"):
    """Register an active agent, add its grants, and mint a JWT bound to
    ``project_id`` (global when project_id is None)."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="grok",
        display_name="Grok",
        origin="external-selfjoin",
        handle="@grok",
    )
    cid = rec["canonical_id"]
    await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="grok", project_id=project_id
    )
    return cid, token


@pytest.mark.asyncio
class TestCheckAgentScopeForProject:
    async def test_returns_canonical_id_for_matching_project(self, token_app):
        cid, token = await _mint(token_app, project_id="prj-1")
        req = _FakeRequest(token_app, token)
        got = await check_agent_scope_for_project(req, "project_tasks", "prj-1")
        assert got == cid

    async def test_403_on_project_mismatch(self, token_app):
        _cid, token = await _mint(token_app, project_id="prj-1")
        req = _FakeRequest(token_app, token)
        with pytest.raises(HTTPException) as exc:
            await check_agent_scope_for_project(req, "project_tasks", "prj-OTHER")
        assert exc.value.status_code == 403

    async def test_403_on_missing_project_claim(self, token_app):
        # Global token (no project_id claim) must never satisfy a project check.
        _cid, token = await _mint(token_app, project_id=None)
        req = _FakeRequest(token_app, token)
        with pytest.raises(HTTPException) as exc:
            await check_agent_scope_for_project(req, "project_tasks", "prj-1")
        assert exc.value.status_code == 403

    async def test_403_on_missing_scope(self, token_app):
        _cid, token = await _mint(
            token_app, scopes=("a2a_receive",), project_id="prj-1"
        )
        req = _FakeRequest(token_app, token)
        with pytest.raises(HTTPException) as exc:
            await check_agent_scope_for_project(req, "project_tasks", "prj-1")
        assert exc.value.status_code == 403

    async def test_none_when_no_bearer(self, token_app):
        req = _FakeRequest(token_app, token=None)
        got = await check_agent_scope_for_project(req, "project_tasks", "prj-1")
        assert got is None


@pytest.mark.asyncio
class TestCheckAgentScopeUnchanged:
    """The shared-verifier refactor must not alter check_agent_scope."""

    async def test_returns_canonical_id_ignoring_project(self, token_app):
        cid, token = await _mint(token_app, project_id="prj-1")
        req = _FakeRequest(token_app, token)
        # project binding is irrelevant to the non-project check.
        got = await check_agent_scope(req, "project_tasks")
        assert got == cid

    async def test_none_when_no_bearer(self, token_app):
        req = _FakeRequest(token_app, token=None)
        assert await check_agent_scope(req, "project_tasks") is None

    async def test_403_on_missing_scope(self, token_app):
        _cid, token = await _mint(
            token_app, scopes=("a2a_receive",), project_id="prj-1"
        )
        req = _FakeRequest(token_app, token)
        with pytest.raises(HTTPException) as exc:
            await check_agent_scope(req, "project_tasks")
        assert exc.value.status_code == 403
