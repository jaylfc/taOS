"""Tests for active_project_grants predicate (extracted from agent_token_auth).

The predicate must be usable outside an HTTP request: no Request, no fastapi
exceptions, just pure data (registry record + grants list) -> set of project_ids.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from datetime import datetime, timezone

from tinyagentos.agent_token_auth import (
    active_project_grants,
    _grant_unexpired,
    check_agent_scope_for_project,
)


class _FakeRegistry:
    def __init__(self, records: dict[str, dict]):
        self._records = records

    async def get(self, canonical_id: str):
        return self._records.get(canonical_id)


class _FakeGrantsStore:
    def __init__(self, grants: dict[str, list[dict]]):
        self._grants = grants

    async def list_grants(self, canonical_id: str):
        return self._grants.get(canonical_id, [])


class _FakeRequest:
    def __init__(self, app, token: str | None = None):
        self.app = app
        self.headers = {}
        if token is not None:
            self.headers["Authorization"] = f"Bearer {token}"


@pytest_asyncio.fixture
async def setup_stores():
    """Provides a fresh registry and grants store for each test."""
    registry = _FakeRegistry({})
    grants = _FakeGrantsStore({})
    return registry, grants


class TestGrantUnexpired:
    """Unit tests for the shared _grant_unexpired helper (fail-closed)."""

    def test_none_expiry_is_unexpired(self):
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert _grant_unexpired(None, now) is True

    def test_future_expiry_is_unexpired(self):
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert _grant_unexpired("2026-01-01T00:00:00+00:00", now) is True

    def test_past_expiry_is_expired(self):
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert _grant_unexpired("2024-01-01T00:00:00+00:00", now) is False

    def test_naive_timestamp_assumed_utc(self):
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert _grant_unexpired("2026-01-01T00:00:00", now) is True

    def test_unparseable_expiry_fails_closed(self):
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert _grant_unexpired("not-a-date", now) is False
        assert _grant_unexpired("", now) is False
        assert _grant_unexpired(123, now) is False


@pytest.mark.asyncio
class TestActiveProjectGrants:
    """Tests for active_project_grants predicate."""

    async def test_returns_empty_for_missing_record(self, setup_stores):
        registry, grants = setup_stores
        result = await active_project_grants(registry, grants, "missing-cid", "project_tasks")
        assert result == set()

    async def test_returns_empty_for_inactive_record(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "revoked"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": None}
        ]
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks")
        assert result == set()

    async def test_returns_empty_for_suspended_record(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "suspended"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": None}
        ]
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks")
        assert result == set()

    async def test_returns_empty_for_pending_record(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "pending"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": None}
        ]
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks")
        assert result == set()

    async def test_returns_project_ids_for_active_record_with_matching_scope(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "active"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": None},
            {"scope": "project_tasks", "project_id": "prj-2", "expires_at": None},
        ]
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks")
        assert result == {"prj-1", "prj-2"}

    async def test_excludes_expired_grants(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "active"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": "2026-01-01T00:00:00+00:00"},
            {"scope": "project_tasks", "project_id": "prj-2", "expires_at": "2024-01-01T00:00:00+00:00"},
        ]
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks", now=now)
        assert result == {"prj-1"}

    async def test_excludes_grants_with_unparseable_expiry(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "active"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": "not-a-date"},
            {"scope": "project_tasks", "project_id": "prj-2", "expires_at": None},
        ]
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks", now=now)
        assert result == {"prj-2"}

    async def test_filters_by_scope(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "active"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": None},
            {"scope": "a2a_receive", "project_id": "prj-2", "expires_at": None},
        ]
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks")
        assert result == {"prj-1"}

    async def test_skips_grants_without_project_id(self, setup_stores):
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "active"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": None, "expires_at": None},
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": None},
        ]
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks")
        assert result == {"prj-1"}

    async def test_is_per_project_grant_on_prj_1_only(self, setup_stores):
        """Grant on prj-1 only -> {'prj-1'}."""
        registry, grants = setup_stores
        registry._records["cid-1"] = {"canonical_id": "cid-1", "status": "active"}
        grants._grants["cid-1"] = [
            {"scope": "project_tasks", "project_id": "prj-1", "expires_at": None},
            {"scope": "project_tasks", "project_id": "prj-2", "expires_at": "2024-01-01T00:00:00+00:00"},
        ]
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = await active_project_grants(registry, grants, "cid-1", "project_tasks", now=now)
        assert result == {"prj-1"}


class _FakeAppState:
    def __init__(self, grants):
        self.agent_grants = grants


class _FakeApp:
    def __init__(self, grants):
        self.state = _FakeAppState(grants)


@pytest.mark.asyncio
class TestCheckAgentScopeForProjectGrantPredicate:
    """Tests for check_agent_scope_for_project grant gating (unbound vs bound)."""

    async def test_403_for_unbound_grant_on_project_request(self, setup_stores):
        """An unbound grant (project_id None) cannot authorize a project-scoped request."""
        _registry, grants = setup_stores
        grants._grants["cid-1"] = [
            {"scope": "decisions_write", "project_id": None, "expires_at": None}
        ]
        app = _FakeApp(grants)
        req = _FakeRequest(app)

        with patch("tinyagentos.agent_token_auth._verify_agent_scope", return_value=("cid-1", {})):
            with pytest.raises(HTTPException) as exc:
                await check_agent_scope_for_project(req, "decisions_write", "prj-x")
            assert exc.value.status_code == 403

    async def test_succeeds_for_unbound_grant_with_project_id_none(self, setup_stores):
        """Unbound grant with project_id=None succeeds when called with project_id=None."""
        _registry, grants = setup_stores
        grants._grants["cid-1"] = [
            {"scope": "decisions_write", "project_id": None, "expires_at": None}
        ]
        app = _FakeApp(grants)
        req = _FakeRequest(app)

        with patch("tinyagentos.agent_token_auth._verify_agent_scope", return_value=("cid-1", {})):
            got = await check_agent_scope_for_project(req, "decisions_write", None)
            assert got == "cid-1"

    async def test_bound_grant_matches_project_and_rejects_other(self, setup_stores):
        """Grant bound to prj-x succeeds for prj-x and raises 403 for prj-y."""
        _registry, grants = setup_stores
        grants._grants["cid-1"] = [
            {"scope": "decisions_write", "project_id": "prj-x", "expires_at": None}
        ]
        app = _FakeApp(grants)
        req = _FakeRequest(app)

        with patch("tinyagentos.agent_token_auth._verify_agent_scope", return_value=("cid-1", {})):
            got = await check_agent_scope_for_project(req, "decisions_write", "prj-x")
            assert got == "cid-1"

            with pytest.raises(HTTPException) as exc:
                await check_agent_scope_for_project(req, "decisions_write", "prj-y")
            assert exc.value.status_code == 403
