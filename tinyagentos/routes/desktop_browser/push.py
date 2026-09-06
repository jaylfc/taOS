"""Web Push send helper for taOS BrowserApp.

Exposes one public coroutine — ``send()`` — that delivers a push notification
to every (or a filtered subset of) push subscriptions for a user.

Design notes
------------
* pywebpush.webpush() is synchronous (uses requests).  We run it in the
  default asyncio thread executor so the event loop is never blocked.
* VAPID keys are NOT instantiated here — the caller (Task 8 triggers) passes
  the (public_key_b64url, private_key_pem_str) tuple through.  This keeps
  push.py testable without a filesystem dependency.
* Rate limiting is a simple in-memory sliding-window (30 calls/min/user).
  It is per send() invocation, not per subscription.
* 410 Gone from the push service means the subscription is permanently
  invalid — we delete it from the store.  Other 4xx/5xx are transient
  failures; we count them and move on.
* Secrets (auth_key, p256dh_key, private PEM) are never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

import pywebpush
from pywebpush import WebPushException

from tinyagentos.routes.desktop_browser.store import BrowserStore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VAPID_SUB = "mailto:admin@taos.local"
_SEND_TIMEOUT = 5.0  # seconds per upstream push-service call
_RATE_LIMIT_PER_MIN = 30
_WINDOW_SECONDS = 60


# ---------------------------------------------------------------------------
# In-memory sliding-window rate limiter (30 sends/min/user, single-process)
# ---------------------------------------------------------------------------


class _RateLimiter:
    """30 sends/min/user, sliding window.  In-memory, single-process."""

    def __init__(self, limit: int = 30, window_seconds: int = 60) -> None:
        self._limit = limit
        self._window = window_seconds
        self._timestamps: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: str) -> bool:
        """True if under limit, False if denied."""
        async with self._lock:
            now = time.monotonic()
            q = self._timestamps[user_id]
            while q and q[0] < now - self._window:
                q.popleft()
            if len(q) >= self._limit:
                return False
            q.append(now)
            return True


_rate_limiter = _RateLimiter(limit=_RATE_LIMIT_PER_MIN, window_seconds=_WINDOW_SECONDS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def send(
    user_id: str,
    payload: dict,
    *,
    devices: list[str] | None = None,
    store: BrowserStore,
    vapid: tuple[str, str],  # (public_key_b64url, private_key_pem_str)
) -> dict:
    """Send a push notification to all subscriptions of a user.

    ``payload`` is JSON-serialized to a UTF-8 string before sending.
    ``devices`` filters to specific device_ids (default = all).
    Returns ``{"sent": int, "failed": int, "removed": int}``.

    On 410 Gone: the subscription is removed from the store.
    On other 4xx/5xx or 429: counted as failed, subscription kept.
    On rate-limit hit (30/min/user): all subs in the rejected slice are
    counted as failed and no upstream calls are made.
    """
    _, private_pem = vapid
    data_str = json.dumps(payload)

    # Fetch subscriptions for this user.
    all_subs = await store.list_push_subscriptions(user_id)
    if devices is not None:
        subs = [s for s in all_subs if s["device_id"] in devices]
    else:
        subs = all_subs

    if not subs:
        return {"sent": 0, "failed": 0, "removed": 0}

    # Rate-limit check — ONCE per send() invocation, not per device.
    allowed = await _rate_limiter.acquire(user_id)
    if not allowed:
        _log.warning("push: rate limit exceeded for user %r (%d subs dropped)", user_id, len(subs))
        return {"sent": 0, "failed": len(subs), "removed": 0}

    loop = asyncio.get_running_loop()
    results = await asyncio.gather(
        *[_send_one(sub, data_str, private_pem, store, loop) for sub in subs],
        return_exceptions=True,
    )

    sent = failed = removed = 0
    for r in results:
        if isinstance(r, Exception):
            # gather(return_exceptions=True) surfaces unexpected exceptions here.
            _log.warning("push: unexpected error sending to a subscription: %s", r)
            failed += 1
        elif r == "sent":
            sent += 1
        elif r == "removed":
            removed += 1
        else:
            failed += 1

    return {"sent": sent, "failed": failed, "removed": removed}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _send_one(
    sub: dict,
    data_str: str,
    private_pem: str,
    store: BrowserStore,
    loop: asyncio.AbstractEventLoop,
) -> str:
    """Send to one subscription.  Returns "sent", "failed", or "removed"."""
    endpoint = sub["endpoint"]
    parsed = urlsplit(endpoint)
    aud = f"{parsed.scheme}://{parsed.netloc}"

    subscription_info = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": sub["p256dh_key"],
            "auth": sub["auth_key"],
        },
    }
    vapid_claims = {
        "sub": _VAPID_SUB,
        "aud": aud,
    }

    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                None,
                _sync_send,
                subscription_info,
                data_str,
                private_pem,
                vapid_claims,
            ),
            timeout=_SEND_TIMEOUT,
        )
        return "sent"
    except WebPushException as exc:
        response = exc.response
        status = getattr(response, "status_code", None)
        ep_hash = hash(endpoint) & 0xFFFFFFFF
        if status == 410:
            _log.info("push: endpoint gone (hash=%08x), removing subscription", ep_hash)
            await store.delete_push_subscription_by_endpoint(endpoint)
            return "removed"
        _log.warning(
            "push: delivery failed for endpoint hash=%08x status=%s: %s",
            ep_hash, status, exc.message,
        )
        return "failed"
    except asyncio.TimeoutError:
        ep_hash = hash(endpoint) & 0xFFFFFFFF
        _log.warning("push: send timed out for endpoint hash=%08x", ep_hash)
        return "failed"
    except Exception as exc:
        ep_hash = hash(endpoint) & 0xFFFFFFFF
        _log.warning("push: unexpected error for endpoint hash=%08x: %s", ep_hash, exc)
        return "failed"


def _sync_send(
    subscription_info: dict,
    data: str,
    private_pem: str,
    vapid_claims: dict,
) -> None:
    """Blocking pywebpush call — run in executor."""
    pywebpush.webpush(
        subscription_info=subscription_info,
        data=data,
        vapid_private_key=private_pem,
        vapid_claims=vapid_claims,
        timeout=_SEND_TIMEOUT,
    )
