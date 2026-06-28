"""Orphaned agent-container reconciliation (BUG B, part 2).

Covers:
  (a) find_orphaned_agent_containers returns taOS containers with no backing
      agent (live or archived) and skips backed ones,
  (b) non-taOS containers and backed containers are never returned,
  (c) reconcile clean=True snapshots-then-archives an orphan (restore point),
  (d) idempotency: a second pass finds nothing once archived.
"""
from __future__ import annotations

import pytest

from tinyagentos.agent_orphan_reconcile import (
    find_orphaned_agent_containers,
    reconcile_orphaned_agent_containers,
)


class _FakeConfig:
    def __init__(self, agents=None, archived=None):
        self.agents = agents or []
        self.archived_agents = archived or []


@pytest.mark.asyncio
class TestFindOrphans:
    async def test_unbacked_taos_container_is_orphan(self, monkeypatch):
        # list_all_taos_containers already prefix-filters to taOS containers,
        # so the fake returns only those (non-taOS filtering is covered in
        # test_containers.TestResolveAgentContainer).
        async def fake_list(*_a, **_k):
            return [
                {"name": "taos-agent-live", "project": "default"},   # backed (live)
                {"name": "taos-agent-ghost", "project": "user-9"},   # orphan
            ]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list
        )
        config = _FakeConfig(agents=[{"name": "live"}])
        orphans = await find_orphaned_agent_containers(config)
        names = {o["name"] for o in orphans}
        assert names == {"taos-agent-ghost"}

    async def test_legacy_named_live_agent_container_is_not_orphan(self, monkeypatch):
        """A live agent's legacy ``taos-<slug>`` container counts as backed."""
        async def fake_list(*_a, **_k):
            return [{"name": "taos-live", "project": "default"}]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list
        )
        config = _FakeConfig(agents=[{"name": "live"}])
        assert await find_orphaned_agent_containers(config) == []

    async def test_archived_container_is_not_orphan(self, monkeypatch):
        async def fake_list(*_a, **_k):
            return [{"name": "taos-test", "project": "user-9"}]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list
        )
        config = _FakeConfig(
            archived=[{"archived_slug": "test", "container_name": "taos-test"}]
        )
        assert await find_orphaned_agent_containers(config) == []


@pytest.mark.asyncio
class TestReconcileClean:
    async def test_report_only_does_not_snapshot(self, client, app, monkeypatch):
        async def fake_list(*_a, **_k):
            return [{"name": "taos-agent-ghost", "project": "default"}]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list
        )

        async def fake_snapshot(name, snapshot_name):
            raise AssertionError("report-only must not snapshot")
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot)

        resp = await client.post("/api/agents/reconcile-orphan-containers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["containers"][0]["action"] == "found"

    async def test_clean_snapshots_then_archives(self, client, app, monkeypatch):
        async def fake_list(*_a, **_k):
            return [{"name": "taos-agent-ghost", "project": "default"}]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list
        )

        snap_calls = []

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot(name, snapshot_name):
            snap_calls.append(name)
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot)

        resp = await client.post(
            "/api/agents/reconcile-orphan-containers?clean=true"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["containers"][0]["action"] == "archived"
        # The orphan was snapshotted (restore point) before being archived.
        assert snap_calls == ["taos-agent-ghost"]

        # An archive entry now backs the former orphan, so a second pass (with
        # the container still "present" in the fake listing) treats it as backed.
        archived = (await client.get("/api/agents/archived")).json()
        assert any(e.get("container_name") == "taos-agent-ghost" for e in archived)

        second = await reconcile_orphaned_agent_containers(
            _Req(app), clean=False
        )
        assert second == []

    async def test_bare_prefix_container_is_ignored(self, client, app, monkeypatch):
        """A container named exactly ``taos-`` / ``taos-agent-`` strips to an
        empty slug -- it must be skipped (no archive row) while a normal
        ``taos-agent-<slug>`` orphan is still archived."""
        async def fake_list(*_a, **_k):
            return [
                {"name": "taos-agent-", "project": "default"},        # empty slug
                {"name": "taos-", "project": "default"},              # empty slug
                {"name": "taos-agent-ghost", "project": "default"},   # valid orphan
            ]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list
        )

        snap_calls = []

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot(name, snapshot_name):
            snap_calls.append(name)
            return {"success": True, "output": ""}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot)

        resp = await client.post(
            "/api/agents/reconcile-orphan-containers?clean=true"
        )
        assert resp.status_code == 200
        data = resp.json()
        by_name = {c["name"]: c["action"] for c in data["containers"]}
        assert by_name["taos-agent-"] == "skipped_empty_slug"
        assert by_name["taos-"] == "skipped_empty_slug"
        assert by_name["taos-agent-ghost"] == "archived"

        # Only the valid orphan was snapshotted/archived; the bare-prefix ones
        # produced no archive row.
        assert snap_calls == ["taos-agent-ghost"]
        archived = (await client.get("/api/agents/archived")).json()
        assert all(e.get("archived_slug") for e in archived)
        assert not any(
            e.get("container_name") in {"taos-agent-", "taos-"} for e in archived
        )

    async def test_clean_leaves_orphan_when_snapshot_fails(
        self, client, app, monkeypatch
    ):
        """No restore point -> never destroy; orphan is reported, not archived."""
        async def fake_list(*_a, **_k):
            return [{"name": "taos-agent-ghost", "project": "default"}]
        monkeypatch.setattr(
            "tinyagentos.containers.list_all_taos_containers", fake_list
        )

        async def fake_stop(name, force=False):
            return {"success": True, "output": ""}

        async def fake_snapshot(name, snapshot_name):
            return {"success": False, "output": "Error: pool offline"}

        monkeypatch.setattr("tinyagentos.containers.stop_container", fake_stop)
        monkeypatch.setattr("tinyagentos.containers.snapshot_create", fake_snapshot)

        resp = await client.post(
            "/api/agents/reconcile-orphan-containers?clean=true"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["containers"][0]["action"] == "snapshot_failed"
        # No archive entry was written for a container we could not snapshot.
        archived = (await client.get("/api/agents/archived")).json()
        assert not any(
            e.get("container_name") == "taos-agent-ghost" for e in archived
        )


class _Req:
    """Minimal request stand-in exposing app.state for the reconcile helper."""

    def __init__(self, app):
        self.app = app
