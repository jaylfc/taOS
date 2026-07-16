"""Hub directory proxy (hub social slice 3) -- taos.my contract.

The taos.my side (requests inbox, accepted-edge rows, presence TTL) is the
contract; here we assert the controller proxy forwards to the right upstream
paths, relays the session cookie, and degrades to 503 when the account service
is unconfigured. Edge authorization and rate-limit behavior are server-side
(the directory enforces them); these tests confirm the responses pass through
verbatim, which is all the proxy is responsible for.
"""
import httpx
import pytest

_UPSTREAM = "https://taos.my"


def _patch_upstream(monkeypatch, handler):
    orig = httpx.AsyncClient.request

    async def routed(self, method, url, **kw):
        if str(url).startswith(_UPSTREAM):
            return await handler(method, str(url), **kw)
        return await orig(self, method, url, **kw)

    monkeypatch.setattr("httpx.AsyncClient.request", routed)


class _FakeResp:
    def __init__(self, content=b"{}", status=200, headers=None):
        self.content = content
        self.status_code = status
        self.headers = httpx.Headers(headers or {})


@pytest.mark.asyncio
async def test_hub_requests_post_forwards_signed_intro(client, monkeypatch):
    """POST /api/account/hub/requests forwards to {base}/api/hub/requests with the
    signed intro body and the session cookie pass-through."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["cookie"] = kw.get("headers", {}).get("Cookie", "")
        captured["body"] = kw.get("content", b"").decode("utf-8")
        return _FakeResp(content=b'{"request_id":"r1"}',
                         headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.post(
        "/api/account/hub/requests",
        json={"to": "peerFP", "author": "meFP", "intro": "hi", "sig": "cc"},
    )
    assert r.status_code == 200
    assert r.json()["request_id"] == "r1"
    assert captured["url"] == "https://taos.my/api/hub/requests"
    assert captured["method"] == "POST"
    assert "taos_session" not in captured["cookie"]
    assert captured["cookie"] == ""
    assert "peerFP" in captured["body"]


@pytest.mark.asyncio
async def test_hub_requests_get_forwards(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        return _FakeResp(content=b'{"inbox":[]}', headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/hub/requests")
    assert r.status_code == 200
    assert captured["url"] == "https://taos.my/api/hub/requests"
    assert captured["method"] == "GET"


@pytest.mark.asyncio
async def test_hub_request_accept_forwards_rid(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["url"] = url
        return _FakeResp(content=b'{"peer":"peerFP","endpoints":[]}',
                         headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/hub/requests/rid-ABC_123/accept")
    assert r.status_code == 200
    assert r.json()["peer"] == "peerFP"
    assert captured["url"] == "https://taos.my/api/hub/requests/rid-ABC_123/accept"


@pytest.mark.asyncio
async def test_hub_request_decline_forwards_rid(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["url"] = url
        return _FakeResp(content=b'{"peer":"peerFP"}',
                         headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/hub/requests/rid-ABC_123/decline")
    assert r.status_code == 200
    assert captured["url"] == "https://taos.my/api/hub/requests/rid-ABC_123/decline"


@pytest.mark.asyncio
async def test_hub_presence_post_forwards(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["url"] = url
        captured["body"] = kw.get("content", b"").decode("utf-8")
        return _FakeResp(content=b'{"ok":true}', headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/hub/presence",
                         json={"endpoints": ["wss://x"], "sig": "s"})
    assert r.status_code == 200
    assert captured["url"] == "https://taos.my/api/hub/presence"
    assert "endpoints" in captured["body"]


@pytest.mark.asyncio
async def test_hub_presence_get_forwards_username(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["url"] = url
        return _FakeResp(content=b'{"endpoints":["wss://x"]}',
                         headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/hub/presence?username=alice")
    assert r.status_code == 200
    assert r.json()["endpoints"] == ["wss://x"]
    assert captured["url"] == "https://taos.my/api/hub/presence?username=alice"


@pytest.mark.asyncio
async def test_hub_edge_revoke_forwards(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["url"] = url
        return _FakeResp(content=b'{"ok":true}', headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/hub/edges/revoke", json={"peer": "peerFP"})
    assert r.status_code == 200
    assert captured["url"] == "https://taos.my/api/hub/edges/revoke"


# --- input validation (path-traversal / SSRF guard) ---

@pytest.mark.asyncio
async def test_hub_request_accept_rejects_bad_rid(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    for bad in ["..%2f..%2f", "r1;evil", "r1%20x", "x" * 65]:
        r = await client.post(f"/api/account/hub/requests/{bad}/accept")
        assert r.status_code in (400, 404), bad
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_hub_request_decline_rejects_bad_rid(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    for bad in ["a/b", "../auth/me", "x" * 65]:
        r = await client.post(f"/api/account/hub/requests/{bad}/decline")
        assert r.status_code in (400, 404), bad
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_hub_presence_get_rejects_bad_username(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    for bad in ["a/b", "a?x=1", "../auth/me", "a;b", "x" * 65]:
        r = await client.get(f"/api/account/hub/presence?username={bad}")
        assert r.status_code == 400, bad
        assert "invalid username" in r.json().get("error", "")
    assert called["n"] == 0


# --- degrade to 503 when the account service is unconfigured ---

@pytest.mark.asyncio
async def test_hub_slice3_503_when_blanked(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    paths = [
        ("POST", "/api/account/hub/requests"),
        ("GET", "/api/account/hub/requests"),
        ("POST", "/api/account/hub/requests/rid-1/accept"),
        ("POST", "/api/account/hub/requests/rid-1/decline"),
        ("POST", "/api/account/hub/presence"),
        ("GET", "/api/account/hub/presence?username=alice"),
        ("POST", "/api/account/hub/edges/revoke"),
    ]
    for method, path in paths:
        r = await client.request(method, path)
        assert r.status_code == 503, (method, path)
        assert "not configured" in r.json().get("error", "")
    assert called["n"] == 0


# --- edge authorization + rate-limit behavior pass through (server-side) ---

@pytest.mark.asyncio
async def test_hub_presence_denied_without_accepted_edge(client, monkeypatch):
    """The directory returns 403 when the requester holds no accepted edge to the
    target (design: presence requires "an accepted edge"). The proxy must pass that
    403 through verbatim rather than masking it."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")

    async def handler(method, url, **kw):
        return _FakeResp(content=b'{"error":"not authorized"}', status=403,
                         headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/hub/presence?username=alice")
    assert r.status_code == 403
    assert r.json()["error"] == "not authorized"


@pytest.mark.asyncio
async def test_hub_requests_rate_limited_passes_through(client, monkeypatch):
    """The directory rate-limits requests per sender and per target (design, slice
    3). The proxy must relay the 429 so the app can surface "slow down"."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")

    async def handler(method, url, **kw):
        return _FakeResp(content=b'{"error":"rate limited"}', status=429,
                         headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.post(
        "/api/account/hub/requests",
        json={"to": "peerFP", "author": "meFP", "intro": "hi", "sig": "cc"},
    )
    assert r.status_code == 429
    assert r.json()["error"] == "rate limited"
