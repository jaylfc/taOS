"""A signed-in caller's POST must be rejected without a CSRF header.

WHY THIS FILE EXISTS, AND WHY IT IS NAMED THIS WAY
--------------------------------------------------
`tests/conftest.py` used to install an autouse fixture that replaced
`verify_csrf` with a no-op for every test file whose path did NOT contain the
substring "test_csrf".  Measured on origin/dev: 788 test files, exactly ONE
inside the carve-out, so 787 ran against an app in which the CSRF dependency
did nothing -- and 223 of those issue POSTs.  That fixture is what hid #2081
(the CSRF login lockout): a first repro written as an ordinary test returned
303 and PASSED, and the tell was that the CONTROL passed identically -- the
shape you get when the input never reaches the system under test.

This module's name deliberately does NOT contain "test_csrf", so it sits
OUTSIDE the old carve-out.  That is the point: it is exactly the file the old
fixture would have silenced, and it must be RED under that fixture.

WHY THE ASSERTIONS DRIVE THE REAL CALLER
----------------------------------------
Checking `verify_csrf.__name__` catches the one bypass mechanism we happen to
have today (patching the module attribute) and nothing else: it cannot see an
`app.dependency_overrides` entry, and any stub that copies the right `__name__`
satisfies it.  Route introspection is barely better -- when the bypass IS
active the installed callable is a conftest function, so a locator keyed on the
csrf module reports "not wired", which cannot tell a DISABLED check from a
REMOVED one.

So these tests sign in for real and then act as that signed-in browser would.
Rejected-vs-accepted is the property the real caller depends on, and it is the
granularity at which the defect lives.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import AuthManager

PASSWORD = "correct horse battery staple"
USERNAME = "tester"

# POST /auth/logout carries `Depends(verify_csrf)` explicitly AND sits under a
# router registered with the router-wide `dependencies=_csrf` list, so it
# exercises both wiring paths.  On success it answers 303; a rejected request
# answers 403.  Those are distinguishable without inspecting a body.
PROTECTED_PATH = "/auth/logout"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """A real app with one real account.

    `register_all_routers` does `from ... import verify_csrf` and freezes the
    resulting object into `Depends(...)` at `include_router` time, so what ends
    up installed is decided by whatever the module attribute was AT BUILD TIME.
    Building the app inside the test is what makes an active bypass observable.
    """
    from tinyagentos.app import create_app

    monkeypatch.setenv("TINYAGENTOS_DATA_DIR", str(tmp_path))
    built = create_app()
    mgr = AuthManager(tmp_path)
    mgr.setup_user(USERNAME, "Bring-up Test", "", PASSWORD)
    built.state.auth = mgr
    return built


@pytest_asyncio.fixture()
async def signed_in(app):
    """A client holding a REAL session, exactly as a browser would.

    A hand-made `taos_session` cookie would be rejected by auth before the CSRF
    dependency ever runs, so the test would pass on the wrong status code.
    Signing in for real is what puts the request into the state `verify_csrf`
    is meant to guard.

    `/auth/login` is in `_CREDENTIAL_PATHS` and is CSRF-exempt by design, which
    is why signing in needs no token of its own.
    """
    transport = ASGITransport(app=app, client=("127.0.0.1", 51234))
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        resp = await client.post(
            "/auth/login", json={"username": USERNAME, "password": PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        # Both halves of the double-submit pair must be present, or the
        # assertions below would be measuring a cookie-less caller -- which
        # verify_csrf exempts, so they would pass vacuously.
        assert client.cookies.get("taos_session"), "no session cookie after sign-in"
        assert client.cookies.get("csrf_token"), "no csrf cookie after sign-in"
        yield client


@pytest.mark.asyncio
async def test_signed_in_post_without_a_csrf_header_is_rejected(signed_in):
    """The RED case.

    Under the old autouse bypass this returns 303 (the logout succeeds) instead
    of 403, which is the correct and intended report: the app under test had a
    CSRF dependency that could not reject anything.
    """
    resp = await signed_in.post(PROTECTED_PATH)

    assert resp.status_code == 403, (
        f"expected 403 for a signed-in POST with no X-CSRF-Token, got "
        f"{resp.status_code}. The CSRF dependency on the built app is not "
        f"enforcing."
    )


@pytest.mark.asyncio
async def test_signed_in_post_with_a_mismatched_csrf_header_is_rejected(signed_in):
    """A check that only looks for PRESENCE is not a double-submit check."""
    resp = await signed_in.post(PROTECTED_PATH, headers={"X-CSRF-Token": "b" * 64})

    assert resp.status_code == 403, (
        f"expected 403 for a mismatched X-CSRF-Token, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_signed_in_post_with_a_matching_csrf_header_succeeds(signed_in):
    """The positive control.

    Without this, a dependency that rejected unconditionally would satisfy both
    tests above while breaking every legitimate caller -- and a 403-only pair
    of assertions could not tell the two apart.
    """
    token = signed_in.cookies.get("csrf_token")

    resp = await signed_in.post(PROTECTED_PATH, headers={"X-CSRF-Token": token})

    assert resp.status_code == 303, resp.text


def test_this_module_is_outside_any_csrf_bypass():
    """Guard the guard.

    Every assertion above is only evidence while this module runs against the
    REAL `verify_csrf`.  If a future conftest change (or a rename of this file)
    puts it back under a bypass, the tests above would go green while measuring
    nothing -- the exact failure this card exists to remove.  Assert the
    installed function's identity rather than trusting the filename.
    """
    from tinyagentos.middleware import csrf

    assert csrf.verify_csrf.__module__ == "tinyagentos.middleware.csrf", (
        f"verify_csrf is patched to {csrf.verify_csrf!r} -- this module is "
        f"running under a CSRF bypass and proves nothing."
    )
    assert csrf.verify_csrf.__name__ == "verify_csrf"
