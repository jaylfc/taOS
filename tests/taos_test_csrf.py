"""Shared CSRF plumbing for tests that build their own ``httpx.AsyncClient``.

Tests run against the REAL ``verify_csrf`` (see ``tests/conftest.py``).  A test
client that injects a ``taos_session`` cookie is therefore a signed-in browser,
and a signed-in browser that POSTs without ``X-CSRF-Token`` gets a 403 — exactly
as it would in production.

The fix is NOT to switch the check off.  It is to make the test client do what
the SPA does: read the ``csrf_token`` cookie and echo it into the header.  The
double-submit check is safe because a third-party origin cannot READ the cookie
and so cannot set a matching header; a same-origin caller reads its own cookie
and echoes it.  That is the caller these tests stand in for.

Usage at a client construction site::

    from taos_test_csrf import csrf_event_hooks

    AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )

This lives in its own module rather than in ``conftest.py`` on purpose:
``tests/`` is not a package and there are several ``conftest.py`` files, so a
bare ``from conftest import ...`` binds whichever one the run happens to put on
``sys.path`` first — which is what makes ``pytest tests/`` abort collection
today (card ``tsk-xplzqy``).  A uniquely named module cannot collide.
"""

import functools
import weakref
from http.cookies import SimpleCookie

from tinyagentos.auth import AuthManager
from tinyagentos.middleware.csrf import (
    csrf_secret_for_auth,
    get_csrf_secret,
    mint_csrf_token,
    verify_bound,
)

__all__ = [
    "TEST_CSRF_TOKEN",
    "bound_csrf_token",
    "echo_csrf_cookie_into_header",
    "csrf_event_hooks",
    "sync_csrf_event_hooks",
    "arm_test_client",
]


# Fallback only, for a session this module cannot trace to an AuthManager
# (a hand-made token no manager issued).  ``verify_csrf`` now also requires the
# cookie to be bound to the session (tsk-34qgmt), so a real session always gets
# a token minted through ``mint_csrf_token`` instead.
TEST_CSRF_TOKEN = "testsuite-csrf-token-0123456789abcdef0123456789abcdef"


def bound_csrf_token(app, session_token: str) -> str:
    """A ``csrf_token`` bound to ``session_token``, minted the way the server does.

    For tests that set the cookie and header by hand next to a real
    ``taos_session``.
    """
    return mint_csrf_token(get_csrf_secret(app), session_token)


# Session token -> the AuthManager that issued it, so the request hook (which
# sees only the outgoing request, never the app) can mint a token bound to the
# session with that app's key.  Weak values: a finished test's app is not kept
# alive by this table.
_SESSION_OWNERS: "weakref.WeakValueDictionary[str, AuthManager]" = (
    weakref.WeakValueDictionary()
)


def _track_sessions(create_session):
    @functools.wraps(create_session)
    def wrapper(self, *args, **kwargs):
        token = create_session(self, *args, **kwargs)
        _SESSION_OWNERS[token] = self
        return token

    wrapper._taos_test_csrf_tracked = True
    return wrapper


if not getattr(AuthManager.create_session, "_taos_test_csrf_tracked", False):
    AuthManager.create_session = _track_sessions(AuthManager.create_session)


def _session_secret(session_token: str):
    owner = _SESSION_OWNERS.get(session_token)
    return csrf_secret_for_auth(owner) if owner is not None else None


def _with_cookie(cookie_header: str, name: str, value: str) -> str:
    """``cookie_header`` with ``name`` set to ``value`` (replaced or appended)."""
    pairs = [p.strip() for p in cookie_header.split(";") if p.strip()]
    pairs = [p for p in pairs if p.split("=", 1)[0].strip() != name]
    pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _echo(request) -> None:
    """Do what the SPA does: echo the CSRF cookie into the request header.

    Safe methods are skipped because ``verify_csrf`` exempts them, and an
    explicitly-set header is never overwritten — a test that deliberately sends
    a wrong or missing token is asserting something about CSRF itself and must
    keep control of it.
    """
    if request.method.upper() in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return
    if "x-csrf-token" in request.headers:
        return

    # Read the value actually on the wire rather than assuming the seeded
    # constant.  CSRFMiddleware issues a fresh token whenever a request arrives
    # without one, so a client that picked one up mid-test holds a value this
    # module never chose; echoing the constant would then MISMATCH and 403.
    cookie_header = request.headers.get("cookie", "")
    jar = SimpleCookie()
    jar.load(cookie_header)
    morsel = jar.get("csrf_token")
    session = jar.get("taos_session")
    secret = _session_secret(session.value) if session is not None else None

    if morsel is not None:
        if secret is not None and not verify_bound(secret, morsel.value, session.value):
            # The jar holds a token that is not bound to this session (the
            # pre-session one, picked up before the test injected a session).
            # A browser would have had it re-issued by the first response that
            # carried the session; these clients inject the session directly,
            # so stand in for that response.
            token = mint_csrf_token(secret, session.value)
            request.headers["cookie"] = _with_cookie(cookie_header, "csrf_token", token)
            request.headers["x-csrf-token"] = token
            return
        request.headers["x-csrf-token"] = morsel.value
        return

    # No session cookie either, so `verify_csrf` exempts this request outright
    # and there is nothing to satisfy.  Returning here is not just an
    # optimisation: injecting a csrf_token cookie makes CSRFMiddleware treat the
    # caller as already holding one and skip ISSUING it, so a test that signs in
    # and then reads `csrf_token` off the login response would read an empty
    # string and send an empty header.  That is the same shape that broke
    # `test_csrf.py::test_csrf_cookie_set_on_response` when this was seeded on
    # the cookie jar instead.
    if session is None:
        return

    # No CSRF cookie yet.  A real browser would already hold one, because
    # CSRFMiddleware sets it on the first response — but these clients inject
    # the session directly instead of navigating, so no response has reached the
    # jar.  Supply BOTH halves, which is the state navigation would have
    # produced.
    #
    # Injected here rather than seeded on the client's cookie jar so that SAFE
    # requests still arrive without a CSRF cookie.  The jar applies to every
    # request, and a GET carrying the cookie makes CSRFMiddleware skip issuing
    # one — which silently breaks the tests asserting that it does issue one.
    token = (
        mint_csrf_token(secret, session.value) if secret is not None else TEST_CSRF_TOKEN
    )
    request.headers["cookie"] = _with_cookie(cookie_header, "csrf_token", token)
    request.headers["x-csrf-token"] = token


async def echo_csrf_cookie_into_header(request) -> None:
    """Async hook, for ``httpx.AsyncClient``."""
    _echo(request)


def echo_csrf_cookie_into_header_sync(request) -> None:
    """Sync hook, for ``httpx.Client`` and ``fastapi.testclient.TestClient``.

    A sync client rejects a coroutine hook, so the two transports need separate
    entry points around the same body.
    """
    _echo(request)


def csrf_event_hooks() -> dict:
    """``event_hooks=`` value that makes an AsyncClient behave like the SPA.

    Returns a fresh dict per call so a client cannot mutate the shared one.
    """
    return {"request": [echo_csrf_cookie_into_header]}


def sync_csrf_event_hooks() -> dict:
    """The same, for a SYNC client (``TestClient``)."""
    return {"request": [echo_csrf_cookie_into_header_sync]}


def arm_test_client(client):
    """Attach the sync hook to an already-built ``TestClient``.

    ``TestClient.__init__`` has a fixed signature and takes no ``event_hooks``,
    so the hook goes on after construction.  Returns the client for chaining.
    """
    client.event_hooks = sync_csrf_event_hooks()
    return client
