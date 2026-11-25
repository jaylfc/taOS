"""Collab invite delivery refuses peer-advertised endpoints that point inward.

A friended peer chooses the endpoint URLs on its peer link. The invite POST
used a plain httpx client, so a peer could aim it at loopback, the cloud
metadata address or a LAN host. It now goes through the shared SSRF guard.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tinyagentos.routes import project_invites


class _Contacts:
    def __init__(self, endpoints):
        self._eps = endpoints

    async def get_contact(self, contact_id):
        return {"contact_id": contact_id, "status": "active", "hub_username": "peer"}

    async def get_peer_link(self, contact_id):
        return {"endpoints": self._eps, "outbound_token": "tok"}


async def _mint(client, app, monkeypatch, endpoints):
    r = await client.post("/api/projects", json={"name": "Collab", "slug": "collab-guard"})
    pid = r.json()["id"]
    app.state.contacts_store = _Contacts(endpoints)
    monkeypatch.setattr("tinyagentos.peer.resolve_local_identity_id", lambda _d: "hub:me")
    return await client.post(
        f"/api/projects/{pid}/invites/collab", json={"contact_id": "hub:peer", "pin_required": False}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:6969", "http://169.254.169.254", "http://localhost:80", "http://[::1]:80", "http://100.64.1.2:6969"],
)
async def test_inward_endpoints_are_not_posted_to(client, app, monkeypatch, url):
    posted: list[str] = []

    class _Spy:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            posted.append(url)
            return SimpleNamespace(status_code=200)

    # Any client built without the guard would land here and record the URL.
    monkeypatch.setattr("httpx.AsyncClient", _Spy)
    resp = await _mint(client, app, monkeypatch, [{"url": url, "priority": 1}])
    assert resp.status_code == 200, resp.text
    assert posted == [], f"posted to {posted}"
    assert resp.json()["delivered"] is False


@pytest.mark.asyncio
async def test_guarded_client_is_the_one_used(client, app, monkeypatch):
    seen = {}

    class _Ok:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **k):
            seen["url"], seen["headers"] = url, k.get("headers")
            return SimpleNamespace(status_code=200)

    import tinyagentos.routes.desktop_browser.ssrf as ssrf

    monkeypatch.setattr(ssrf, "validate_url_or_raise", lambda u, **k: ["203.0.113.5"])
    monkeypatch.setattr(ssrf, "guarded_async_client", lambda **k: _Ok())
    resp = await _mint(client, app, monkeypatch, [{"url": "https://peer.example", "priority": 1}])
    assert resp.json()["delivered"] is True
    assert seen["url"] == "https://peer.example/api/peer/inbox"
    assert seen["headers"]["Authorization"] == "Bearer tok"
