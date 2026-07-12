import httpx
import pytest

_UPSTREAM = "https://taos.my"


def _patch_upstream(monkeypatch, handler):
    """Patch httpx.AsyncClient.request so ONLY the proxy's upstream call (an
    absolute taos.my URL) is intercepted; the test client's own ASGI calls
    (relative URLs) pass through to the real request."""
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
async def test_account_me_503_when_explicitly_blanked(client, monkeypatch):
    # An explicit blank override disables the proxy (the dev/off-taos.my state).
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "")
    r = await client.get("/api/account/me")
    assert r.status_code == 503
    assert "not configured" in r.json().get("error", "")


def test_base_url_defaults_to_taos_my(monkeypatch):
    from tinyagentos.routes.account_proxy import _base_url
    # Unset (the normal instance) uses the production account service.
    monkeypatch.delenv("TAOS_ACCOUNT_BASE_URL", raising=False)
    assert _base_url() == "https://taos.my"
    # An explicit blank disables it; a real override is honored (trailing / trimmed).
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "")
    assert _base_url() is None
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://staging.taos.my/")
    assert _base_url() == "https://staging.taos.my"


@pytest.mark.asyncio
async def test_account_me_forwards_body_and_relays_cookie(client, monkeypatch):
    """/api/account/me forwards to {base}/api/auth/me; the upstream body and
    content-type pass through verbatim and the session cookie is relayed."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my/")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        return _FakeResp(
            content=b'{"user_id":"u1","email":"a@b.c","taosgo":{"status":"none"}}',
            headers={
                "content-type": "application/json",
                "set-cookie": "taosgo_session=abc; Path=/",
            },
        )

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/me")
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.c"
    assert captured["url"] == "https://taos.my/api/auth/me"
    assert captured["method"] == "GET"
    assert "taosgo_session=abc" in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_set_cookie_rescoped_to_proxy_origin(client, monkeypatch):
    """A taos.my cookie carrying Domain + Secure must be rescoped to this
    origin, or the browser rejects it: Domain is stripped, and Secure is
    dropped because the test client speaks http. Other attrs are preserved."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")

    async def handler(method, url, **kw):
        return _FakeResp(
            content=b"{}",
            headers={
                "content-type": "application/json",
                "set-cookie": "taosgo_session=abc; Path=/; Domain=taos.my; Secure; HttpOnly",
            },
        )

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/me")
    sc = r.headers.get("set-cookie", "")
    assert "taosgo_session=abc" in sc
    assert "domain=" not in sc.lower()
    assert "secure" not in sc.lower()
    assert "HttpOnly" in sc


@pytest.mark.asyncio
async def test_account_me_503_when_upstream_unreachable(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")

    async def handler(method, url, **kw):
        raise httpx.ConnectError("down")

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/me")
    assert r.status_code == 503
    assert "unreachable" in r.json().get("error", "")


@pytest.mark.asyncio
async def test_secure_kept_when_x_forwarded_proto_https_and_trusted(client, monkeypatch):
    """Behind a TLS-terminating proxy the request scheme is http but the browser
    leg is https (X-Forwarded-Proto). When the deployment trusts that header
    (TAOS_TRUST_FORWARDED_PROTO), the cookie Secure attr must be kept."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    monkeypatch.setenv("TAOS_TRUST_FORWARDED_PROTO", "1")

    async def handler(method, url, **kw):
        return _FakeResp(
            headers={"content-type": "application/json", "set-cookie": "s=1; Path=/; Secure"}
        )

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/me", headers={"x-forwarded-proto": "https"})
    assert "secure" in r.headers.get("set-cookie", "").lower()


@pytest.mark.asyncio
async def test_x_forwarded_proto_ignored_when_untrusted(client, monkeypatch):
    """Without the trust opt-in, X-Forwarded-Proto is client-spoofable, so it is
    ignored and Secure is dropped over the plain-http test connection."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    monkeypatch.delenv("TAOS_TRUST_FORWARDED_PROTO", raising=False)

    async def handler(method, url, **kw):
        return _FakeResp(
            headers={"content-type": "application/json", "set-cookie": "s=1; Path=/; Secure"}
        )

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/me", headers={"x-forwarded-proto": "https"})
    assert "secure" not in r.headers.get("set-cookie", "").lower()


@pytest.mark.asyncio
async def test_redirect_location_is_relayed(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")

    async def handler(method, url, **kw):
        return _FakeResp(content=b"", status=302, headers={"location": "https://taos.my/login"})

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/me", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "https://taos.my/login"


@pytest.mark.asyncio
async def test_cluster_join_request_forwards(client, monkeypatch):
    """/api/account/cluster/join/request forwards to {base}/api/cluster/join/request
    with the body and the session cookie passed through."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["cookie"] = kw.get("headers", {}).get("Cookie", "")
        return _FakeResp(
            content=b'{"request_id":"r1","status":"pending","expires_at":"2026-01-01T00:00:00Z"}',
            headers={"content-type": "application/json"},
        )

    _patch_upstream(monkeypatch, handler)
    # Do NOT override the cookie header: the `client` fixture carries the taOS
    # controller session that the /api/account/* proxy (rightly) requires, and
    # that session cookie is what gets forwarded upstream.
    r = await client.post(
        "/api/account/cluster/join/request",
        json={"device_name": "Mac", "ttl": "10m"},
    )
    assert r.status_code == 200
    assert r.json()["request_id"] == "r1"
    assert captured["url"] == "https://taos.my/api/cluster/join/request"
    assert captured["method"] == "POST"
    assert captured["cookie"]  # the session cookie was passed through


@pytest.mark.asyncio
async def test_cluster_join_poll_forwards_rid(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["url"] = url
        return _FakeResp(content=b'{"status":"pending"}', headers={"content-type": "application/json"})

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/cluster/join/requests/req-ABC_123/poll")
    assert r.status_code == 200
    assert captured["url"] == "https://taos.my/api/cluster/join/requests/req-ABC_123/poll"


@pytest.mark.asyncio
async def test_cluster_join_rejects_bad_request_id(client, monkeypatch):
    """A request_id that could inject path/query never reaches the upstream: a
    malformed token is rejected at the validator (400), and an encoded-slash
    traversal attempt fails to match the route (404). Either way, no upstream
    call is made (path-traversal / SSRF guard)."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    # The encoded-slash case is blocked at routing (404); the others reach the
    # validator and are rejected (400). Neither reaches the upstream.
    for bad in ["..%2f..%2fauth%2fme", "r1;evil", "r1%20x", "x" * 65]:
        r = await client.post(f"/api/account/cluster/join/requests/{bad}/approve")
        assert r.status_code in (400, 404), bad
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_cluster_join_503_when_explicitly_blanked(client, monkeypatch):
    # Unset now defaults to taos.my; an explicit blank disables the proxy (503).
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "")
    r = await client.get("/api/account/cluster/join/requests")
    assert r.status_code == 503


# --- Account subdomain actions (account model slice 3) ---
@pytest.mark.asyncio
async def test_subdomains_check_forwards_name(client, monkeypatch):
    """GET /api/account/subdomains/check?name=x forwards to
    {base}/api/subdomains/check?name=x, relaying the session cookie."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["cookie"] = kw.get("headers", {}).get("Cookie", "")
        return _FakeResp(
            content=b'{"available":true}',
            headers={"content-type": "application/json"},
        )

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/subdomains/check?name=mybiz")
    assert r.status_code == 200
    assert r.json()["available"] is True
    assert captured["url"] == "https://taos.my/api/subdomains/check?name=mybiz"
    assert captured["method"] == "GET"
    assert captured["cookie"]  # session cookie passed through


@pytest.mark.asyncio
async def test_subdomains_claim_forwards_body(client, monkeypatch):
    """POST /api/account/subdomains/claim forwards to {base}/api/subdomains/claim
    with the validated name in the body and the session cookie passed through."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["cookie"] = kw.get("headers", {}).get("Cookie", "")
        captured["body"] = kw.get("content", b"").decode("utf-8")
        return _FakeResp(
            content=b'{"id":"c1","name":"mybiz","status":"active"}',
            headers={"content-type": "application/json"},
        )

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/subdomains/claim", json={"name": "mybiz"})
    assert r.status_code == 200
    assert r.json()["name"] == "mybiz"
    assert captured["url"] == "https://taos.my/api/subdomains/claim"
    assert captured["method"] == "POST"
    assert captured["cookie"]
    assert "mybiz" in captured["body"]


@pytest.mark.asyncio
async def test_subdomains_release_forwards_body(client, monkeypatch):
    """POST /api/account/subdomains/release forwards to
    {base}/api/subdomains/release with the validated name in the body."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    captured: dict[str, str] = {}

    async def handler(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["body"] = kw.get("content", b"").decode("utf-8")
        return _FakeResp(
            content=b'{"name":"mybiz","status":"released"}',
            headers={"content-type": "application/json"},
        )

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/subdomains/release", json={"name": "mybiz"})
    assert r.status_code == 200
    assert captured["url"] == "https://taos.my/api/subdomains/release"
    assert captured["method"] == "POST"
    assert "mybiz" in captured["body"]


@pytest.mark.asyncio
async def test_subdomains_check_503_when_unconfigured(client, monkeypatch):
    """An explicit blank override disables the proxy; every subdomain action
    returns 503 without contacting the upstream."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    r = await client.get("/api/account/subdomains/check?name=mybiz")
    assert r.status_code == 503
    assert "not configured" in r.json().get("error", "")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_subdomains_claim_503_when_unconfigured(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/subdomains/claim", json={"name": "mybiz"})
    assert r.status_code == 503
    assert "not configured" in r.json().get("error", "")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_subdomains_check_rejects_invalid_name(client, monkeypatch):
    """A name that could inject path/query never reaches the upstream: it is
    rejected at the validator (400) and no upstream call is made."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    for bad in ["..%2f..%2f", "a/b", "a?x=1", "a;b", "../auth/me", "x" * 65]:
        r = await client.get(f"/api/account/subdomains/check?name={bad}")
        assert r.status_code == 400, bad
        assert "invalid name" in r.json().get("error", "")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_subdomains_claim_rejects_invalid_name(client, monkeypatch):
    """A malformed name in the claim/release body is rejected (400) before the
    upstream is contacted."""
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    for bad in ["a/b", "a?x=1", "../auth/me", "x" * 65, ""]:
        r = await client.post("/api/account/subdomains/claim", json={"name": bad})
        assert r.status_code == 400, bad
    # A non-JSON / non-dict body is also rejected (400), not forwarded.
    r = await client.post(
        "/api/account/subdomains/claim",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_subdomains_release_rejects_invalid_name(client, monkeypatch):
    monkeypatch.setenv("TAOS_ACCOUNT_BASE_URL", "https://taos.my")
    called = {"n": 0}

    async def handler(method, url, **kw):
        called["n"] += 1
        return _FakeResp()

    _patch_upstream(monkeypatch, handler)
    r = await client.post("/api/account/subdomains/release", json={"name": "a/b"})
    assert r.status_code == 400
    assert called["n"] == 0
