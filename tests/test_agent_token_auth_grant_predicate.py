"""Tests for active_project_grants predicate (extracted from agent_token_auth).

The predicate must be usable outside an HTTP request: no Request, no fastapi
exceptions, just pure data (registry record + grants list) -> set of project_ids.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone

from tinyagentos.agent_token_auth import (
    active_project_grants,
    _grant_unexpired,
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


@pytest_asyncio.fixture
async def setup_stores():
    """Provides a fresh registry and grants store for each test."""
    registry = _FakeRegistry({})
    grants = _FakeGrantsStore({})
    return registry, grants


class TestGrantUnExpried:
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