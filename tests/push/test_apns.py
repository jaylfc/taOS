import base64
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
# tsk-42q2qf: provider-token reuse + 410 Unregistered handling
# ---------------------------------------------------------------------------


def _test_key_pem() -> str:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _counting_mint(monkeypatch) -> list[int]:
    """Replace build_apns_jwt with a counting passthrough; returns the counter."""
    from tinyagentos.push import apns as apns_mod

    real = apns_mod.build_apns_jwt
    mints = [0]

    def counted(**kwargs):
        mints[0] += 1
        return real(**kwargs)

    monkeypatch.setattr(apns_mod, "build_apns_jwt", counted)
    return mints


def _decode_iat(jwt: str) -> int:
    """Decode the `iat` claim out of a JWT built by build_apns_jwt."""
    payload_b64 = jwt.split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))["iat"]


def _sender_with(handler, pem: str):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = HttpApnsSender(
        key_pem=pem, key_id="KID", team_id="TID", bundle_id="com.taos.app",
        host="api.push.apple.com", client=client,
    )
    return sender, client


@pytest.mark.asyncio
async def test_provider_token_is_reused_across_pushes(monkeypatch):
    # Apple caps provider-token GENERATION: minting one per push earns
    # 403 TooManyProviderTokenUpdates and refuses pushes account-wide, so a
    # burst must reuse one cached token rather than mint per request.
    mints = _counting_mint(monkeypatch)
    auths = set()

    def handler(req: httpx.Request) -> httpx.Response:
        auths.add(req.headers.get("authorization"))
        return httpx.Response(200)

    sender, client = _sender_with(handler, _test_key_pem())
    for _ in range(50):
        assert await sender.send("devtoken", {"aps": {}}) is True
    assert mints[0] == 1, f"expected 1 JWT mint across 50 pushes, got {mints[0]}"
    assert len(auths) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_token_refreshes_after_the_window(monkeypatch):
    # The cached token must still be refreshed on a timer: Apple expires a
    # provider token after an hour, so a long-lived process that never reminted
    # would eventually push with a dead token.
    from tinyagentos.push import apns as apns_mod

    mints = _counting_mint(monkeypatch)
    clock = [1_700_000_000.0]
    monkeypatch.setattr(apns_mod.time, "time", lambda: clock[0])

    sender, client = _sender_with(lambda req: httpx.Response(200), _test_key_pem())
    await sender.send("devtoken", {"aps": {}})
    clock[0] += 10 * 60
    await sender.send("devtoken", {"aps": {}})
    assert mints[0] == 1, f"expected 1 mint 10 minutes in, got {mints[0]}"

    clock[0] += 55 * 60
    await sender.send("devtoken", {"aps": {}})
    assert mints[0] == 2, f"expected a refresh past the window, got {mints[0]} mints"
    await client.aclose()


@pytest.mark.asyncio
async def test_410_raises_unregistered_carrying_reason_and_apns_id():
    # 410 Unregistered is Apple's permanent "this device token is dead" signal.
    # Collapsing it into a plain False means the token is retried forever, so
    # the sender must raise a distinguishable error the caller can prune on.
    from tinyagentos.push.apns import ApnsUnregistered

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410,
            json={"reason": "Unregistered", "timestamp": 1700000000000},
            headers={"apns-id": "AAAA-BBBB"},
        )

    sender, client = _sender_with(handler, _test_key_pem())
    with pytest.raises(ApnsUnregistered) as excinfo:
        await sender.send("deadtoken", {"aps": {}})
    assert excinfo.value.reason == "Unregistered"
    assert excinfo.value.apns_id == "AAAA-BBBB"
    assert excinfo.value.push_token == "deadtoken"
    await client.aclose()


@pytest.mark.asyncio
async def test_failure_reason_is_surfaced_in_logs(caplog):
    # Neither apns-id nor Apple's own `reason` was ever logged, so a refusal was
    # indistinguishable from any other non-200 and could not be diagnosed.
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410, json={"reason": "Unregistered"}, headers={"apns-id": "AAAA-BBBB"},
        )

    sender, client = _sender_with(handler, _test_key_pem())
    with caplog.at_level("WARNING", logger="tinyagentos.push.apns"):
        try:
            await sender.send("deadtoken", {"aps": {}})
        except Exception:
            pass
    assert "Unregistered" in caplog.text
    assert "AAAA-BBBB" in caplog.text
    await client.aclose()


@pytest.mark.asyncio
async def test_non_410_refusal_logs_reason_and_returns_false(caplog):
    # A retryable refusal (bad payload, bad topic) stays a plain False, but the
    # reason must still reach the log.
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"reason": "BadDeviceToken"})

    sender, client = _sender_with(handler, _test_key_pem())
    with caplog.at_level("WARNING", logger="tinyagentos.push.apns"):
        assert await sender.send("devtoken", {"aps": {}}) is False
    assert "BadDeviceToken" in caplog.text
    await client.aclose()


@pytest.mark.asyncio
async def test_expired_provider_token_forces_a_remint(monkeypatch):
    # Caching introduces a new failure mode: if the cached token expires early
    # (clock skew), every push would be refused until the refresh timer fired.
    # Apple's ExpiredProviderToken must therefore invalidate the cache at once.
    mints = _counting_mint(monkeypatch)
    statuses = [403, 200, 200]

    def handler(req: httpx.Request) -> httpx.Response:
        if statuses.pop(0) == 403:
            return httpx.Response(403, json={"reason": "ExpiredProviderToken"})
        return httpx.Response(200)

    sender, client = _sender_with(handler, _test_key_pem())
    assert await sender.send("devtoken", {"aps": {}}) is False
    assert await sender.send("devtoken", {"aps": {}}) is True
    assert await sender.send("devtoken", {"aps": {}}) is True
    # Exactly one extra mint: the expiry invalidates the cache once, and the
    # replacement token is then reused like any other.
    assert mints[0] == 2, f"expected exactly one remint after ExpiredProviderToken, got {mints[0]}"
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_provider_token_forces_a_remint(monkeypatch):
    # InvalidProviderToken is just as permanent as ExpiredProviderToken (a
    # rotated signing key, or a cached token that is otherwise unparseable):
    # every push would be refused for the rest of the 50-minute cache window
    # unless this also invalidates the cache immediately.
    mints = _counting_mint(monkeypatch)
    statuses = [403, 200, 200]

    def handler(req: httpx.Request) -> httpx.Response:
        if statuses.pop(0) == 403:
            return httpx.Response(403, json={"reason": "InvalidProviderToken"})
        return httpx.Response(200)

    sender, client = _sender_with(handler, _test_key_pem())
    assert await sender.send("devtoken", {"aps": {}}) is False
    assert await sender.send("devtoken", {"aps": {}}) is True
    assert await sender.send("devtoken", {"aps": {}}) is True
    assert mints[0] == 2, f"expected exactly one remint after InvalidProviderToken, got {mints[0]}"
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_token_iat_never_regresses_after_backward_clock_step(monkeypatch):
    # A wall clock that steps backward (a bad NTP correction) must not pin the
    # next token's iat to the regressed time. Apple checks iat against its OWN
    # correct clock: a regressed iat combined with a full fresh 50-minute local
    # cache window can let this cache keep reusing the token until Apple's real
    # elapsed-since-iat time is already past the true one-hour limit, well
    # before the local refresh timer would ever fire.
    from tinyagentos.push import apns as apns_mod

    clock = [1_700_000_000.0]
    monkeypatch.setattr(apns_mod.time, "time", lambda: clock[0])

    sender, client = _sender_with(lambda req: httpx.Response(200), _test_key_pem())
    await sender.send("devtoken", {"aps": {}})
    iat1 = _decode_iat(sender._jwt)
    assert iat1 == int(clock[0])

    # The clock steps backward by 15 minutes and never corrects (a permanent
    # skew), forcing an immediate remint (age goes negative).
    clock[0] -= 15 * 60
    await sender.send("devtoken", {"aps": {}})
    iat2 = _decode_iat(sender._jwt)
    assert iat2 >= iat1, f"iat regressed from {iat1} to {iat2} after a backward clock step"
    await client.aclose()


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
