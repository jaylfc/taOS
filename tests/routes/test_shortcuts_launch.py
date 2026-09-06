import pytest


@pytest.fixture
def agent_with_shortcuts(seeded_agent_factory):
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


def test_admin_launch_returns_redirect_url(
    test_client, admin_auth_headers, agent_with_shortcuts
):
    """Admin launching shortcut idx=0 must get a redirect_url and expires_in=30."""
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.post(
        f"/api/agents/{agent_id}/shortcuts/0/launch",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "redirect_url" in data
    assert data["expires_in"] == 30
    assert "redeem" in data["redirect_url"]


def test_launch_includes_ticket_in_redirect_url(
    test_client, admin_auth_headers, agent_with_shortcuts
):
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.post(
        f"/api/agents/{agent_id}/shortcuts/0/launch",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    redirect_url = resp.json()["redirect_url"]
    assert "t=" in redirect_url


def test_launch_denied_for_chat_only_user(
    test_client, chat_only_auth_headers, agent_with_shortcuts
):
    """User without required capability must get 403."""
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.post(
        f"/api/agents/{agent_id}/shortcuts/0/launch",
        headers=chat_only_auth_headers,
    )
    assert resp.status_code == 403


def test_launch_idx_out_of_range_returns_404(
    test_client, admin_auth_headers, agent_with_shortcuts
):
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.post(
        f"/api/agents/{agent_id}/shortcuts/99/launch",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404


def test_launch_unknown_agent_returns_404(test_client, admin_auth_headers):
    resp = test_client.post(
        "/api/agents/ghost-id/shortcuts/0/launch",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404


def test_unauthenticated_launch_returns_401(test_client, agent_with_shortcuts):
    agent_id = agent_with_shortcuts["id"]
    resp = test_client.post(f"/api/agents/{agent_id}/shortcuts/0/launch")
    assert resp.status_code == 401
