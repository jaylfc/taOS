import time
import pytest
from tinyagentos.shortcuts.tickets import mint_ticket, JtiTracker

SIGNING_KEY = b"test-signing-key-32-bytes-padded"
WORKER_URL = "http://127.0.0.1:6969"


def _make_token(agent_id="agent-1", idx=0, scope="container-terminal", ttl=30):
    _, token = mint_ticket(
        agent_id=agent_id,
        shortcut_idx=idx,
        scope=scope,
        signing_key=SIGNING_KEY,
        worker_url=WORKER_URL,
        ttl=ttl,
    )
    return token


def test_valid_ticket_sets_cookie_and_redirects(test_client, patch_worker_signing_key):
    """A valid ticket must set taos_shortcut cookie and 302 to /shortcut/..."""
    token = _make_token()
    resp = test_client.get(f"/redeem?t={token}", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "/shortcut/" in location
    cookies = resp.cookies
    assert "taos_shortcut" in cookies


def test_expired_ticket_returns_401(test_client, patch_worker_signing_key):
    token = _make_token(ttl=-1)
    resp = test_client.get(f"/redeem?t={token}", follow_redirects=False)
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_bad_hmac_returns_401(test_client):
    """A token signed with a different key must be rejected."""
    wrong_key = b"wrong-signing-key-32-bytes-paddd"
    _, token = mint_ticket(
        agent_id="x",
        shortcut_idx=0,
        scope="dashboard",
        signing_key=wrong_key,
        worker_url=WORKER_URL,
        ttl=30,
    )
    resp = test_client.get(f"/redeem?t={token}", follow_redirects=False)
    assert resp.status_code == 401


def test_replayed_ticket_returns_401(test_client, patch_worker_signing_key):
    """Second use of the same ticket must be rejected as a replay."""
    token = _make_token()
    test_client.get(f"/redeem?t={token}", follow_redirects=False)
    resp = test_client.get(f"/redeem?t={token}", follow_redirects=False)
    assert resp.status_code == 401
    assert "replay" in resp.json()["detail"].lower()


def test_missing_ticket_returns_422(test_client):
    """Request without t= query param must return 422."""
    resp = test_client.get("/redeem", follow_redirects=False)
    assert resp.status_code == 422


def test_redirect_location_matches_scope_terminal(test_client, patch_worker_signing_key):
    """container-terminal scope must redirect to /shortcut/terminal/..."""
    token = _make_token(scope="container-terminal")
    resp = test_client.get(f"/redeem?t={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "/shortcut/terminal/" in resp.headers["location"]


def test_redirect_location_matches_scope_dashboard(test_client, patch_worker_signing_key):
    """dashboard scope must redirect to /shortcut/dashboard/..."""
    token = _make_token(scope="dashboard")
    resp = test_client.get(f"/redeem?t={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "/shortcut/dashboard/" in resp.headers["location"]
