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


# ---------------------------------------------------------------------------
# tsk-cf7wzc: image + actions wiring for the native decision shell
# ---------------------------------------------------------------------------


def test_build_payload_sets_category_and_mutable_content_for_actions():
    # Approve/reject/add-note decisions must hit the iOS shell's
    # UNNotificationCategory and let the service extension attach an image, so
    # aps.category + aps.mutable-content must both be set.
    payload = build_apns_payload(
        title="Decide",
        body="deploy?",
        actions=[{"id": "approve", "label": "Approve"}],
        category="DECISION_APPROVE_DENY",
    )
    assert payload["aps"]["category"] == "DECISION_APPROVE_DENY"
    assert payload["aps"]["mutable-content"] == 1


def test_build_payload_sets_mutable_content_when_image_present():
    # Rich attachments need the notification service extension to download the
    # image and attach it; APNs only allows that mutation when mutable-content
    # is set, even with no actions.
    payload = build_apns_payload(
        title="Deploy",
        body="queued",
        image="https://cdn.example.com/run/42.png",
    )
    assert payload["aps"]["mutable-content"] == 1
    assert payload["image"] == "https://cdn.example.com/run/42.png"


def test_build_payload_omits_mutable_content_for_plain_alert():
    # A plain alert with no image and no actions must not set mutable-content
    # so the system shows the unmodified payload.
    payload = build_apns_payload(title="Hi", body="there")
    assert "mutable-content" not in payload["aps"]
    assert "image" not in payload
    assert "category" not in payload["aps"]


def test_build_payload_does_not_override_content_available_for_mutable_content():
    # A silent push (content-available) that ALSO carries an image is fine; we
    # must not clobber content-available by enabling mutable-content on top.
    payload = build_apns_payload(
        title="", body="", content_available=True, image="https://x.test/i.png",
    )
    assert payload["aps"]["content-available"] == 1


# ---------------------------------------------------------------------------
# tsk-674fwg: `data` must not silently clobber the explicit image argument
# ---------------------------------------------------------------------------


def test_build_payload_explicit_image_wins_over_data_image():
    # mutable-content is decided from the image the service extension will
    # fetch, so a caller-supplied data["image"] must not replace the explicit
    # argument after that decision was made -- otherwise the payload advertises
    # one image and the flag was computed from another.
    payload = build_apns_payload(
        title="t",
        body="b",
        image="https://example.com/explicit.png",
        data={"image": "https://example.com/from-caller-data.png"},
    )
    assert payload["image"] == "https://example.com/explicit.png"
    assert payload["aps"]["mutable-content"] == 1


def test_build_payload_sets_mutable_content_for_data_supplied_image():
    # The mirror case: no explicit image, but data carries one. The extension
    # still has to download it, so mutable-content must be set from the value
    # that actually lands in the payload.
    payload = build_apns_payload(
        title="t", body="b", data={"image": "https://example.com/from-data.png"},
    )
    assert payload["image"] == "https://example.com/from-data.png"
    assert payload["aps"]["mutable-content"] == 1


def test_build_payload_sets_mutable_content_for_data_supplied_actions():
    # `actions` reaches the payload through `data` as well (that is how
    # notifications_push threads it), so the flag has to follow the merged
    # action set for the same reason it follows the merged image: without
    # mutable-content the extension may not surface the button row.
    payload = build_apns_payload(
        title="t", body="b", data={"actions": [{"id": "approve", "label": "Approve"}]},
    )
    assert payload["actions"] == [{"id": "approve", "label": "Approve"}]
    assert payload["aps"]["mutable-content"] == 1


def test_build_payload_explicit_empty_actions_overrides_data_actions():
    # A caller that explicitly passes actions=[] is overriding stale/prior
    # action data (e.g. re-sending a notification after a decision resolved).
    # `actions or payload.get("actions")` treats [] the same as omitted, so the
    # stale data actions would incorrectly become effective and set
    # mutable-content; an explicit empty list must take precedence instead.
    payload = build_apns_payload(
        title="t",
        body="b",
        data={"actions": [{"id": "approve", "label": "Approve"}]},
        actions=[],
    )
    assert "mutable-content" not in payload["aps"]
    assert payload.get("actions") in (None, [])


def test_build_payload_data_cannot_replace_aps():
    # aps is Apple's reserved envelope built from the explicit arguments; a
    # stray data["aps"] must not overwrite the alert and flags just computed.
    payload = build_apns_payload(
        title="Hi", body="there", data={"aps": {"alert": "hijacked"}},
    )
    assert payload["aps"]["alert"] == {"title": "Hi", "body": "there"}
