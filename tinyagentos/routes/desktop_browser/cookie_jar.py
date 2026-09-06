"""httpx cookie-jar adapter wrapping BrowserCookieStore.

httpx's `httpx.Cookies` is sync. Our `BrowserCookieStore` is async
(SQLCipher behind an asyncio executor). We bridge the two with two
helpers used per-request:

- `load_jar_for_request` — pre-load all cookies relevant to the
  outgoing request's host into a fresh `httpx.Cookies` jar
- `persist_response_cookies` — after the response, persist any
  Set-Cookie back to the encrypted store

This per-request load/persist pattern is the cleanest way to bridge
sync httpx with our async store; it avoids holding a long-lived jar
that would need its own locking. The cost is one extra DB read per
request (cheap — SQLCipher with a small jar is sub-millisecond) and
a write only when the response actually sets cookies.

Multi-user isolation is enforced by always passing user_id +
profile_id through to the underlying store; there is no helper that
loads "all cookies for this host" without those scopes.
"""
from __future__ import annotations

import httpx

from tinyagentos.routes.desktop_browser.store import BrowserCookieStore


async def load_jar_for_request(
    cookie_store: BrowserCookieStore,
    *,
    user_id: str,
    profile_id: str,
    host: str,
) -> httpx.Cookies:
    """Return an httpx.Cookies jar populated with cookies for (user, profile, host)."""
    if not user_id:
        raise ValueError("user_id is required")
    if not profile_id:
        raise ValueError("profile_id is required")

    rows = await cookie_store.get_cookies(
        user_id=user_id, profile_id=profile_id, host=host.lstrip("."),
    )

    jar = httpx.Cookies()
    for row in rows:
        jar.set(
            name=row["name"],
            value=row["value"],
            domain=row["host"],
            path=row["path"],
        )
    return jar


async def persist_response_cookies(
    cookie_store: BrowserCookieStore,
    response_cookies: httpx.Cookies,
    *,
    user_id: str,
    profile_id: str,
) -> None:
    """Write every cookie in the response jar back to the encrypted store."""
    if not user_id:
        raise ValueError("user_id is required")
    if not profile_id:
        raise ValueError("profile_id is required")

    import time
    now = int(time.time())

    for cookie in response_cookies.jar:
        # httpx.Cookies wraps stdlib http.cookiejar.Cookie. Some attributes
        # vary across Python versions; we extract the safe minimum.
        host = (cookie.domain or "").lstrip(".")
        path = cookie.path or "/"
        expires_at = int(cookie.expires) if cookie.expires is not None else None

        # Server explicitly deleting the cookie via past-dated expiry?
        if expires_at is not None and expires_at <= now:
            await cookie_store.delete_cookie(
                user_id=user_id, profile_id=profile_id,
                host=host, path=path, name=cookie.name,
            )
            continue

        await cookie_store.set_cookie(
            user_id=user_id,
            profile_id=profile_id,
            host=host,
            path=path,
            name=cookie.name,
            value=cookie.value or "",
            expires_at=expires_at,
            http_only=False,  # stdlib cookie jar parsing here doesn't reliably
                              # surface HttpOnly; the proxy will set it
                              # correctly via the response-header path
            secure=bool(cookie.secure),
            same_site=None,   # stdlib cookie jar doesn't expose SameSite directly
        )
