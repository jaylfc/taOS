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

from http.cookies import SimpleCookie

__all__ = [
    "TEST_CSRF_TOKEN",
    "echo_csrf_cookie_into_header",
    "csrf_event_hooks",
    "sync_csrf_event_hooks",
    "arm_test_client",
]


# Any value works — the check is that the header equals the cookie — but it must
# look like a token so a failure message is self-explanatory.
TEST_CSRF_TOKEN = "testsuite-csrf-token-0123456789abcdef0123456789abcdef"


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

    if morsel is not None:
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
    if jar.get("taos_session") is None:
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
    request.headers["cookie"] = (
        f"{cookie_header}; csrf_token={TEST_CSRF_TOKEN}"
        if cookie_header
        else f"csrf_token={TEST_CSRF_TOKEN}"
    )
    request.headers["x-csrf-token"] = TEST_CSRF_TOKEN


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
