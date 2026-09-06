from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils

logger = logging.getLogger(__name__)

# Apple caps provider-token GENERATION, not use: minting a fresh token per push
# earns 403 TooManyProviderTokenUpdates and refuses pushes account-wide. A token
# stays valid for an hour, so one cached token is reused and reminted after 50
# minutes -- inside the validity window, and far under the generation cap.
_TOKEN_REFRESH_SECONDS = 50 * 60


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class ApnsUnregistered(Exception):
    """APNs answered 410 Unregistered: this device token is permanently dead.

    Not a delivery failure to retry -- Apple is telling us the app was removed
    or the token no longer belongs to this topic, so the caller must stop
    pushing to it and drop it from the device store. Mirrors the 404/410 prune
    the web-push path performs on WebPushException.
    """

    def __init__(self, push_token: str, *, apns_id: str | None = None, reason: str | None = None):
        # The token is deliberately kept out of the message: it is a device
        # identifier and this exception can reach a log formatter.
        super().__init__(f"APNs 410 Unregistered (reason={reason or 'unknown'})")
        self.push_token = push_token
        self.apns_id = apns_id
        self.reason = reason


def _apns_reason(resp: httpx.Response) -> str | None:
    """Apple returns the failure cause as ``{"reason": "..."}`` in the body."""
    try:
        body = resp.json()
    except ValueError:
        return None
    return body.get("reason") if isinstance(body, dict) else None


class ApnsSender(Protocol):
    async def send(self, push_token: str, payload: dict, *, topic: str | None = None) -> bool:
        """True when APNs accepted the push, False for a retryable refusal.

        Raises ApnsUnregistered when APNs reports the token is permanently dead
        (410), which the caller must handle by dropping the token rather than
        counting it as another failed delivery.
        """
        ...

    async def aclose(self) -> None:
        ...


class NullApnsSender:
    """Used when APNs is unconfigured: logs the intent and reports failure so
    callers treat the device as unreachable rather than assuming delivery."""

    async def send(self, push_token: str, payload: dict, *, topic: str | None = None) -> bool:
        logger.info("APNs not configured; dropping push to %s", push_token[:8])
        return False

    async def aclose(self) -> None:
        # No client to close; present so shutdown can call aclose() uniformly.
        return None


def build_apns_payload(
    *,
    title: str,
    body: str,
    data: dict | None = None,
    content_available: bool = False,
    category: str | None = None,
    actions: list[dict] | None = None,
    image: str | None = None,
) -> dict:
    """Build an APNs payload, letting explicit keyword args win over `data`.

    ``actions``: when the caller passes anything other than the default
    ``None`` (including an explicit empty list ``[]``), it is authoritative
    for `payload["actions"]` too, not only for the mutable-content gate below.
    An explicit `[]` overrides a stale `data["actions"]` (e.g. re-sending a
    notification after a decision resolved) by leaving the key present with
    an empty list, rather than removing it. Omitting `actions` entirely
    (`None`) leaves any `data`-supplied action set untouched.
    """
    # `data` goes down first so the explicit keyword arguments below win over any
    # same-named key the caller happened to put in it. Merging the other way round
    # let a stray data["image"] replace the explicit image *after* mutable-content
    # had already been decided, so the payload advertised one image while the flag
    # was computed from another.
    payload: dict = dict(data or {})
    if image:
        payload["image"] = image
    if actions is not None:
        payload["actions"] = actions
    # `data` may still be the only source of an image or an action set (that is
    # how notifications_push threads both), so read the merged values back rather
    # than trusting the arguments: mutable-content has to follow what the service
    # extension will actually be handed, not what this call was told about.
    effective_image = payload.get("image")
    effective_actions = payload.get("actions")

    aps: dict = {}
    if title or body:
        aps["alert"] = {"title": title, "body": body}
    if content_available:
        aps["content-available"] = 1
    # Apple does not honour a JSON `actions` array; the native shell (tsk-cf7wzc)
    # registers a UNNotificationCategory whose identifier matches the `category`
    # below. Buttons come from the registered category, and tapping one fires a
    # UNUserNotificationCenterDelegate callback the shell routes back to the
    # controller's Decisions answer route.
    if category:
        aps["category"] = category
    # Any rich attachment (image) or action set requires the notification service
    # extension to mutate the payload before display: download the image, attach
    # UNNotificationAttachment, and surface the action buttons. APNs only allows
    # that mutation when `mutable-content` is set.
    if (effective_image or effective_actions) and not content_available:
        aps["mutable-content"] = 1
    # `aps` is Apple's reserved envelope; assigning it last keeps a stray
    # data["aps"] from overwriting the alert and flags computed above.
    payload["aps"] = aps
    return payload


def build_apns_jwt(*, key_pem: str, key_id: str, team_id: str, now: int) -> str:
    header = {"alg": "ES256", "kid": key_id}
    claims = {"iss": team_id, "iat": now}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    der_sig = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = asym_utils.decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input + "." + _b64url(raw_sig)


class HttpApnsSender:
    def __init__(
        self, *, key_pem: str, key_id: str, team_id: str, bundle_id: str,
        host: str = "api.push.apple.com", client: httpx.AsyncClient | None = None,
    ):
        self._key_pem = key_pem
        self._key_id = key_id
        self._team_id = team_id
        self._bundle_id = bundle_id
        self._host = host
        self._client = client or httpx.AsyncClient(http2=True)
        # Only close the client on aclose() if this sender created it; an
        # injected client is owned by the caller.
        self._owns_client = client is None
        # Cached provider token, reused across pushes (see _provider_token).
        self._jwt: str | None = None
        self._jwt_minted_at = 0.0

    def _provider_token(self, now: float) -> str:
        """Return the cached provider token, reminting only when it is stale.

        Minting per push is what Apple refuses with TooManyProviderTokenUpdates,
        so the token is cached and refreshed on a timer instead. A clock that
        moves backwards also counts as stale, so a bad NTP step cannot pin a
        token past its real expiry. No await runs between the staleness check
        and the store, so concurrent senders on one event loop cannot interleave
        into a double mint.
        """
        age = now - self._jwt_minted_at
        if self._jwt is None or not 0 <= age < _TOKEN_REFRESH_SECONDS:
            self._jwt = build_apns_jwt(
                key_pem=self._key_pem, key_id=self._key_id,
                team_id=self._team_id, now=int(now),
            )
            self._jwt_minted_at = now
        return self._jwt

    async def send(self, push_token: str, payload: dict, *, topic: str | None = None) -> bool:
        jwt = self._provider_token(time.time())
        try:
            resp = await self._client.post(
                f"https://{self._host}/3/device/{push_token}",
                headers={
                    "authorization": f"bearer {jwt}",
                    "apns-topic": topic or self._bundle_id,
                    "apns-push-type": "background" if payload.get("aps", {}).get(
                        "content-available"
                    ) else "alert",
                },
                content=json.dumps(payload),
            )
        except httpx.HTTPError:
            logger.warning("APNs send failed for %s", push_token[:8], exc_info=True)
            return False
        if resp.status_code == 200:
            return True
        # Every refusal carries Apple's own cause and a request id; without them
        # a non-200 is undiagnosable, which is why they are logged before the
        # status is turned into a return value.
        apns_id = resp.headers.get("apns-id")
        reason = _apns_reason(resp)
        logger.warning(
            "APNs push refused for %s: status=%s reason=%s apns-id=%s",
            push_token[:8], resp.status_code, reason or "unknown", apns_id or "-",
        )
        if resp.status_code == 410:
            raise ApnsUnregistered(push_token, apns_id=apns_id, reason=reason)
        if resp.status_code == 403 and reason == "ExpiredProviderToken":
            # Caching a token introduces this failure mode: under clock skew the
            # cached token can expire before the refresh timer fires, and every
            # push would then be refused until it did. Drop it so the next send
            # mints a replacement.
            self._jwt = None
        return False

    async def aclose(self) -> None:
        # Close the httpx client only if this sender created it; an injected
        # client stays the caller's responsibility. aclose() is idempotent.
        if self._owns_client and self._client is not None:
            await self._client.aclose()


def apns_sender_from_env() -> ApnsSender:
    key_id = os.environ.get("TAOS_APNS_KEY_ID")
    team_id = os.environ.get("TAOS_APNS_TEAM_ID")
    bundle_id = os.environ.get("TAOS_APNS_BUNDLE_ID")
    key_path = os.environ.get("TAOS_APNS_KEY_PATH")
    # Default to Apple's production gateway; TAOS_APNS_SANDBOX routes dev/TestFlight
    # builds to the sandbox gateway (a token minted for one is rejected by the other).
    host = (
        "api.sandbox.push.apple.com"
        if os.environ.get("TAOS_APNS_SANDBOX", "").lower() in ("1", "true", "yes")
        else "api.push.apple.com"
    )
    if key_id and team_id and bundle_id and key_path and os.path.isfile(key_path):
        # The .p8 signing key is a long-lived credential; warn (do not fail) if it
        # is group/world accessible so the operator can tighten it to 600.
        try:
            if os.stat(key_path).st_mode & 0o077:
                logger.warning(
                    "APNs signing key %s is group/world accessible; tighten to 600",
                    key_path,
                )
        except OSError:
            pass
        with open(key_path) as fh:
            key_pem = fh.read()
        return HttpApnsSender(
            key_pem=key_pem, key_id=key_id, team_id=team_id,
            bundle_id=bundle_id, host=host,
        )
    return NullApnsSender()
