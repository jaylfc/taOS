"""OS-level Web Push for taOS notifications.

Delivers ``NotificationStore.add()`` notifications (in particular the external
agent access-request / consent notifications, source="auth_requests") to an
installed PWA as a real web-push, so the banner shows even when the taOS app is
backgrounded or closed. This is separate from the Browser copilot push under
``routes/desktop_browser`` (a different VAPID key and purpose); only the
send/subscribe patterns are shared.

Design notes
------------
* The subscription store is a small SQLite table keyed by the push endpoint.
  Subscriptions are recorded per user_id. When a notification carries a user_id,
  the send path fans out only to that user's subscriptions; when user_id is None
  (broadcast), it fans out to every subscription for genuinely system-wide
  notices.
* pywebpush.webpush() is synchronous (uses requests). Each send runs in a
  worker thread via asyncio.to_thread so the event loop is never blocked.
* The whole send path is strictly best-effort: a missing VAPID key, no
  subscriptions, or any push error must never raise back into add().
* A 404/410 from the push service means the subscription is permanently gone,
  so its row is pruned.
* Secrets (auth, p256dh, private PEM) are never logged.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from urllib.parse import urlsplit

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

# A real, routable address. Apple's push service is stricter than other push
# services about the VAPID `sub` claim; a reserved .local domain risks rejection.
_VAPID_SUB = "mailto:info@taos.my"
_SEND_TIMEOUT = 5.0  # seconds per upstream push-service call

NOTIF_PUSH_SCHEMA = """
CREATE TABLE IF NOT EXISTS notif_push_subscriptions (
    endpoint TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_push_user ON notif_push_subscriptions(user_id);
"""


class NotificationPushStore(BaseStore):
    """Per-user store of PWA web-push subscriptions for OS notifications."""

    SCHEMA = NOTIF_PUSH_SCHEMA

    async def upsert(self, user_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        """Insert-or-update a subscription, keyed by its push endpoint.

        Idempotent for the owner: re-subscribing the same endpoint refreshes its
        keys and leaves created_at at the first-insert value. A subscription row
        NEVER changes owner: the DO UPDATE is guarded by
        `WHERE user_id = excluded.user_id`, so a conflicting insert from a
        different user is a no-op (it cannot hijack another user's endpoint).
        """
        if not user_id:
            raise ValueError("user_id is required")
        if not endpoint:
            raise ValueError("endpoint is required")
        if not p256dh:
            raise ValueError("p256dh is required")
        if not auth:
            raise ValueError("auth is required")
        assert self._db is not None
        now = int(time.time())
        await self._db.execute(
            "INSERT INTO notif_push_subscriptions (endpoint, user_id, p256dh, auth, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET "
            "  p256dh = excluded.p256dh, "
            "  auth = excluded.auth "
            "WHERE user_id = excluded.user_id",
            (endpoint, user_id, p256dh, auth, now),
        )
        await self._db.commit()

    async def list_all(self) -> list[dict]:
        """Every subscription across users. Used by the send fan-out."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT endpoint, user_id, p256dh, auth, created_at FROM notif_push_subscriptions"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {"endpoint": r[0], "user_id": r[1], "p256dh": r[2], "auth": r[3], "created_at": r[4]}
            for r in rows
        ]

    async def list_for_user(self, user_id: str) -> list[dict]:
        """Subscriptions owned by one user (endpoints + created_at, no secrets)."""
        if not user_id:
            raise ValueError("user_id is required")
        assert self._db is not None
        async with self._db.execute(
            "SELECT endpoint, created_at FROM notif_push_subscriptions WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [{"endpoint": r[0], "created_at": r[1]} for r in rows]

    async def list_all_for_user(self, user_id: str) -> list[dict]:
        """Full subscription rows (including secrets) for one user.

        Used by the send fan-out when scoping a notification to a specific
        user. Returns the same shape as list_all() — endpoint, user_id,
        p256dh, auth, created_at — so _send_one() can use it directly.
        """
        if not user_id:
            raise ValueError("user_id is required")
        assert self._db is not None
        async with self._db.execute(
            "SELECT endpoint, user_id, p256dh, auth, created_at FROM notif_push_subscriptions "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {"endpoint": r[0], "user_id": r[1], "p256dh": r[2], "auth": r[3], "created_at": r[4]}
            for r in rows
        ]

    async def delete_by_endpoint(self, endpoint: str) -> int:
        """Delete the subscription for this endpoint, ignoring ownership.

        For the internal 404/410 send-path cleanup only: a dead endpoint must be
        pruned regardless of which user owns it. NEVER call this from a
        user-facing route (see delete_for_user for that). Returns rows deleted.
        """
        if not endpoint:
            raise ValueError("endpoint is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM notif_push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        await self._db.commit()
        return cursor.rowcount or 0

    async def delete_for_user(self, user_id: str, endpoint: str) -> int:
        """Delete this endpoint only if it belongs to user_id. Returns rows deleted.

        Ownership-scoped so one user cannot unsubscribe (or probe) another
        user's subscription. Returns 0 when the endpoint is absent or owned by
        someone else, so it cannot be used as an ownership oracle.
        """
        if not user_id:
            raise ValueError("user_id is required")
        if not endpoint:
            raise ValueError("endpoint is required")
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM notif_push_subscriptions WHERE endpoint = ? AND user_id = ?",
            (endpoint, user_id),
        )
        await self._db.commit()
        return cursor.rowcount or 0


def _safe_url(raw, fallback: str = "/desktop") -> str:
    """Return raw only if it is an app-root-relative path, else the fallback.

    Guards notificationclick against open-redirect / phishing: the service
    worker eventually passes this to clients.openWindow(). Only a single-leading-
    slash path (``^/[^/]``) is accepted, which forbids a scheme (``https:``,
    ``javascript:``), a protocol-relative ``//host`` target, and a backslash
    that some URL parsers treat as a slash.
    """
    if not isinstance(raw, str) or not raw:
        return fallback
    if not raw.startswith("/"):
        return fallback
    if raw.startswith("//") or "\\" in raw:
        return fallback
    return raw


def _build_payload(row: dict) -> dict:
    """Map a notification row to the web-push payload the service worker reads.

    ``url`` is the notification's own deep link if it carries a safe one in its
    JSON data, else the desktop shell. ``tag`` collapses re-notifies for the
    same source+id so a newer push replaces the older banner.
    """
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    url = _safe_url(data.get("url"))
    return {
        "title": row.get("title") or "taOS",
        "body": row.get("message") or "",
        "tag": f"{row.get('source', 'system')}:{row.get('id', '')}",
        "source": row.get("source", "system"),
        "data": {"url": url},
    }


def _vapid_signing_key(private_pem: str) -> str:
    """Convert the stored VAPID PEM into the base64url-DER form pywebpush wants.

    ``load_or_create_vapid_keypair`` returns the private key as a PEM string, but
    ``pywebpush.webpush(vapid_private_key=...)`` feeds it to ``py_vapid`` via
    ``Vapid.from_string``, which base64-decodes and DER-parses its input. A raw
    PEM fails that as "Could not deserialize key data" on EVERY send, and the
    per-subscription handler swallowed it as a generic warning -- so web push was
    100% broken for every user with no obvious signal. Convert once at fan-out.
    """
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.urlsafe_b64encode(der).decode().rstrip("=")


def _sync_send(subscription_info: dict, data: str, private_pem: str, vapid_claims: dict) -> None:
    """Blocking pywebpush call - run in a worker thread."""
    import pywebpush

    pywebpush.webpush(
        subscription_info=subscription_info,
        data=data,
        vapid_private_key=private_pem,
        vapid_claims=vapid_claims,
        timeout=_SEND_TIMEOUT,
    )


async def _send_one(sub: dict, data_str: str, private_pem: str, store: NotificationPushStore) -> str:
    """Send to one subscription. Returns "sent", "failed", or "removed"."""
    from pywebpush import WebPushException

    endpoint = sub["endpoint"]
    parsed = urlsplit(endpoint)
    subscription_info = {
        "endpoint": endpoint,
        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
    }
    vapid_claims = {"sub": _VAPID_SUB, "aud": f"{parsed.scheme}://{parsed.netloc}"}
    ep_hash = hash(endpoint) & 0xFFFFFFFF
    try:
        await asyncio.to_thread(_sync_send, subscription_info, data_str, private_pem, vapid_claims)
        return "sent"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            logger.info("notif-push: endpoint gone (hash=%08x), pruning subscription", ep_hash)
            await store.delete_by_endpoint(endpoint)
            return "removed"
        logger.warning("notif-push: delivery failed for endpoint hash=%08x status=%s", ep_hash, status)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - best-effort, never propagate
        logger.warning("notif-push: unexpected error for endpoint hash=%08x: %s", ep_hash, exc)
        return "failed"


async def send_web_push(row: dict, *, store: NotificationPushStore, vapid: tuple[str, str] | None) -> dict:
    """Best-effort fan-out of one notification row to every subscription.

    When ``row["user_id"]`` is set, fans out only to that user's subscriptions
    via ``store.list_all_for_user()``. When ``user_id`` is None (broadcast), fans
    out to every subscription via ``store.list_all()`` for genuinely
    system-wide notices.

    Returns {"sent", "failed", "removed"} counts (also useful for tests). Never
    raises: a missing VAPID key or no subscriptions is a no-op, and every
    per-subscription error is caught and counted.
    """
    if not vapid:
        return {"sent": 0, "failed": 0, "removed": 0}
    _, private_pem = vapid
    try:
        signing_key = _vapid_signing_key(private_pem)
    except Exception:  # noqa: BLE001
        # A key that cannot be converted breaks EVERY send, not one. Log it loud
        # (error, not the swallowed per-send warning) so a global misconfig is
        # visible instead of silently disabling all push.
        logger.error("notif-push: VAPID key unusable; web push disabled", exc_info=True)
        return {"sent": 0, "failed": 0, "removed": 0}
    try:
        user_id = row.get("user_id")
        subs = await (store.list_all_for_user(user_id) if user_id else store.list_all())
    except Exception:  # noqa: BLE001 - store read must never break add()
        logger.warning("notif-push: failed to list subscriptions", exc_info=True)
        return {"sent": 0, "failed": 0, "removed": 0}
    if not subs:
        return {"sent": 0, "failed": 0, "removed": 0}

    data_str = json.dumps(_build_payload(row))
    results = await asyncio.gather(
        *[_send_one(sub, data_str, signing_key, store) for sub in subs],
        return_exceptions=True,
    )
    sent = failed = removed = 0
    for r in results:
        if r == "sent":
            sent += 1
        elif r == "removed":
            removed += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "removed": removed}
