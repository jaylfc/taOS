import json
import httpx
import pytest

from tinyagentos.push.apns import (
    NullApnsSender,
    HttpApnsSender,
    build_apns_payload,
    build_apns_jwt,
    apns_sender_from_env,
)


def test_build_payload_alert_and_silent():
    alert = build_apns_payload(title="Hi", body="there", data={"k": "v"})
    assert alert["aps"]["alert"] == {"title": "Hi", "body": "there"}
    assert alert["k"] == "v"

    silent = build_apns_payload(title="", body="", content_available=True)
    assert silent["aps"]["content-available"] == 1


@pytest.mark.asyncio
async def test_null_sender_returns_false():
    assert await NullApnsSender().send("tok", {"aps": {}}) is False


def test_apns_sender_from_env_defaults_to_null(monkeypatch):
    for var in ("TAOS_APNS_KEY_ID", "TAOS_APNS_TEAM_ID", "TAOS_APNS_BUNDLE_ID", "TAOS_APNS_KEY_PATH"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(apns_sender_from_env(), NullApnsSender)


def test_build_jwt_is_three_segments():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    tok = build_apns_jwt(key_pem=pem, key_id="KID", team_id="TID", now=1_700_000_000)
    assert tok.count(".") == 2


@pytest.mark.asyncio
async def test_http_sender_posts_and_reads_status():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["topic"] = req.headers.get("apns-topic")
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = HttpApnsSender(
        key_pem=pem, key_id="KID", team_id="TID", bundle_id="com.taos.app",
        host="api.push.apple.com", client=client,
    )
    ok = await sender.send("devtoken", build_apns_payload(title="Hi", body="yo"))
    assert ok is True
    assert seen["url"].endswith("/3/device/devtoken")
    assert seen["auth"].startswith("bearer ")
    assert seen["topic"] == "com.taos.app"
    await client.aclose()


@pytest.mark.asyncio
async def test_http_sender_self_creates_and_closes_client():
    # HttpApnsSender self-creates an httpx.AsyncClient when none is injected;
    # aclose() must close it so shutdown leaves no open connections.
    sender = HttpApnsSender(
        key_pem="pem", key_id="KID", team_id="TID", bundle_id="com.taos.app",
    )
    assert sender._client.is_closed is False
    await sender.aclose()
    assert sender._client.is_closed is True


@pytest.mark.asyncio
async def test_http_sender_does_not_close_injected_client():
    # When a client is injected, the caller owns it; aclose() must not close it.
    injected = httpx.AsyncClient(http2=True)
    sender = HttpApnsSender(
        key_pem="pem", key_id="KID", team_id="TID", bundle_id="com.taos.app",
        client=injected,
    )
    await sender.aclose()
    assert injected.is_closed is False
    await injected.aclose()


@pytest.mark.asyncio
async def test_null_sender_aclose_is_noop():
    # NullApnsSender exposes a no-op aclose so shutdown can call it uniformly.
    assert await NullApnsSender().aclose() is None
