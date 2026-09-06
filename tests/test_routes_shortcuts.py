"""Endpoint tests for tinyagentos/routes/shortcuts.py."""

from __future__ import annotations

import pytest

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import WorkerInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_worker_registry(monkeypatch) -> None:
    """Inject a test ClusterManager with an enrolled local worker."""
    import tinyagentos.cluster.worker_registry as wr_mod

    mgr = ClusterManager()
    mgr._workers["local"] = WorkerInfo(
        name="local",
        url="http://127.0.0.1:6969",
        worker_url="http://127.0.0.1:6969",
        signing_key=b"test-signing-key-32-bytes-padded",
        platform="local",
    )
    monkeypatch.setattr(wr_mod, "_active_manager", mgr)


def _seed_agent(client, monkeypatch, framework="openclaw", shortcuts=None):
    """Append a test agent to app.state.config.agents and patch FRAMEWORKS.

    The FRAMEWORKS entry is written through ``monkeypatch.setitem`` so the
    process-global dict is restored on teardown -- including DELETING the key
    when ``framework`` was absent (the old ``.get(default)`` silently invented
    one that then leaked for the rest of the process and poisoned order-coupled
    tests such as ``test_openclaw_shortcuts_exact``; see tsk-yjdeom).
    """
    import uuid

    import tinyagentos.frameworks as fw_mod

    if shortcuts is None:
        shortcuts = [
            {
                "kind": "container-terminal",
                "label": "Container shell",
                "icon": "terminal",
                "requires_capability": "agent.shell",
            },
            {
                "kind": "dashboard",
                "label": "Gateway dashboard",
                "icon": "dashboard",
                "requires_capability": "agent.dashboard",
                "port": 18789,
                "path": "/",
                "auth": {"type": "none", "token_source": None},
            },
        ]

    original = fw_mod.FRAMEWORKS.get(framework, {"id": framework, "name": framework})
    patched_entry = {**original, "shortcuts": shortcuts}
    monkeypatch.setitem(fw_mod.FRAMEWORKS, framework, patched_entry)

    agent_id = uuid.uuid4().hex[:12]
    agent = {
        "id": agent_id,
        "name": f"test-agent-{agent_id}",
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#abcdef",
        "framework": framework,
    }
    client._transport.app.state.config.agents.append(agent)
    return agent


# ---------------------------------------------------------------------------
# GET /api/agents/{agent_id}/shortcuts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_shortcuts_returns_200(client, monkeypatch):
    agent = _seed_agent(client, monkeypatch)
    resp = await client.get(f"/api/agents/{agent['id']}/shortcuts")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_shortcuts_returns_list(client, monkeypatch):
    agent = _seed_agent(client, monkeypatch)
    resp = await client.get(f"/api/agents/{agent['id']}/shortcuts")
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_list_shortcuts_admin_sees_all(client, monkeypatch):
    agent = _seed_agent(client, monkeypatch)
    resp = await client.get(f"/api/agents/{agent['id']}/shortcuts")
    data = resp.json()
    assert len(data) == 2
    assert data[0]["kind"] == "container-terminal"
    assert data[0]["idx"] == 0
    assert data[0]["label"] == "Container shell"
    assert data[0]["icon"] == "terminal"
    assert data[1]["kind"] == "dashboard"
    assert data[1]["idx"] == 1


@pytest.mark.asyncio
async def test_list_shortcuts_unknown_agent_returns_404(client):
    resp = await client.get("/api/agents/nonexistent-id/shortcuts")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_shortcuts_by_name(client, monkeypatch):
    agent = _seed_agent(client, monkeypatch)
    resp = await client.get(f"/api/agents/{agent['name']}/shortcuts")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_shortcuts_capability_filtered(client, monkeypatch):
    """Shortcuts requiring agent.admin are not visible to a normal admin."""
    agent = _seed_agent(
        client,
        monkeypatch,
        shortcuts=[
            {
                "kind": "tui",
                "label": "Special tool",
                "icon": "lock",
                "requires_capability": "agent.admin",
            },
        ],
    )
    resp = await client.get(f"/api/agents/{agent['id']}/shortcuts")
    assert resp.status_code == 200
    data = resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_list_shortcuts_no_shortcuts_returns_empty(client, monkeypatch):
    """Agent with a framework that has no shortcuts returns empty list."""
    agent = _seed_agent(client, monkeypatch, shortcuts=[])
    resp = await client.get(f"/api/agents/{agent['id']}/shortcuts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_shortcuts_does_not_expose_requires_capability(client, monkeypatch):
    """The requires_capability field must not leak to the frontend."""
    agent = _seed_agent(client, monkeypatch)
    resp = await client.get(f"/api/agents/{agent['id']}/shortcuts")
    for entry in resp.json():
        assert "requires_capability" not in entry


# ---------------------------------------------------------------------------
# POST /api/agents/{agent_id}/shortcuts/{idx}/launch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_shortcut_returns_redirect_url(client, monkeypatch):
    _setup_worker_registry(monkeypatch)
    agent = _seed_agent(client, monkeypatch)
    resp = await client.post(f"/api/agents/{agent['id']}/shortcuts/0/launch")
    assert resp.status_code == 200
    data = resp.json()
    assert "redirect_url" in data
    assert data["expires_in"] == 30
    assert "redeem" in data["redirect_url"]


@pytest.mark.asyncio
async def test_launch_shortcut_includes_ticket_token(client, monkeypatch):
    _setup_worker_registry(monkeypatch)
    agent = _seed_agent(client, monkeypatch)
    resp = await client.post(f"/api/agents/{agent['id']}/shortcuts/0/launch")
    redirect_url = resp.json()["redirect_url"]
    assert "t=" in redirect_url


@pytest.mark.asyncio
async def test_launch_shortcut_idx_out_of_range(client, monkeypatch):
    _setup_worker_registry(monkeypatch)
    agent = _seed_agent(client, monkeypatch)
    resp = await client.post(f"/api/agents/{agent['id']}/shortcuts/99/launch")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_launch_shortcut_unknown_agent(client, monkeypatch):
    _setup_worker_registry(monkeypatch)
    resp = await client.post("/api/agents/ghost-id/shortcuts/0/launch")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_launch_shortcut_negative_idx(client, monkeypatch):
    _setup_worker_registry(monkeypatch)
    agent = _seed_agent(client, monkeypatch)
    resp = await client.post(f"/api/agents/{agent['id']}/shortcuts/-1/launch")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_launch_shortcut_no_worker_raises_error(client, monkeypatch):
    """Without an active ClusterManager, launch raises RuntimeError.

    _active_manager is a module global in worker_registry that other tests in
    the suite enroll; reset it to None here so this assertion is independent of
    test ordering.
    """
    from tinyagentos.cluster import worker_registry

    monkeypatch.setattr(worker_registry, "_active_manager", None)
    agent = _seed_agent(client, monkeypatch)
    with pytest.raises(RuntimeError, match="No active ClusterManager"):
        await client.post(f"/api/agents/{agent['id']}/shortcuts/0/launch")
