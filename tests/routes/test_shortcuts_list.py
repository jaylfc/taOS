"""
Tests for GET /api/agents/{id}/shortcuts.
Uses the existing test fixtures / app client pattern from the project.
"""
import pytest


@pytest.fixture
def agent_with_shortcuts(seeded_agent_factory):
    """Create a test agent whose framework has shortcuts defined."""
    return seeded_agent_factory(
        framework="openclaw",
        shortcuts=[
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
        ],
    )


def test_admin_sees_all_shortcuts(test_client, admin_auth_headers, agent_with_shortcuts):
    """Admin user with all caps must see all shortcuts."""
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.get(
        f"/api/agents/{agent_id}/shortcuts", headers=admin_auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["kind"] == "container-terminal"
    assert data[0]["idx"] == 0
    assert data[1]["kind"] == "dashboard"
    assert data[1]["idx"] == 1


def test_chat_only_user_sees_no_shortcuts(
    test_client, chat_only_auth_headers, agent_with_shortcuts
):
    """User with only 'chat' cap must see an empty list."""
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.get(
        f"/api/agents/{agent_id}/shortcuts", headers=chat_only_auth_headers
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_agent_shell_cap_only(
    test_client, shell_only_auth_headers, agent_with_shortcuts
):
    """User with agent.shell cap only sees the terminal shortcut, not dashboard."""
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.get(
        f"/api/agents/{agent_id}/shortcuts", headers=shell_only_auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    kinds = [s["kind"] for s in data]
    assert "container-terminal" in kinds
    assert "dashboard" not in kinds


def test_unknown_agent_returns_404(test_client, admin_auth_headers):
    resp = test_client.get(
        "/api/agents/nonexistent-id/shortcuts", headers=admin_auth_headers
    )
    assert resp.status_code == 404


def test_unauthenticated_returns_401(test_client, agent_with_shortcuts):
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.get(f"/api/agents/{agent_id}/shortcuts")
    assert resp.status_code == 401
