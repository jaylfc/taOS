"""Verify the desktop_browser router is mounted on the FastAPI app.

PR 1 mounts an empty router so future PRs can add routes against an
established prefix. This test only checks the router object reaches the
app — it does not exercise any endpoints.
"""
from __future__ import annotations


def test_desktop_browser_router_present_in_app(app):
    """Verify create_app() actually mounted the desktop_browser router.

    The router is empty in PR 1 (PR 2+ adds routes), so we can't check
    for specific paths yet. What we CAN check: the router object reachable
    via `from tinyagentos.routes.desktop_browser import router` is the same
    object that was passed to one of the app's `include_router` calls.
    The cleanest detection is: `router.routes` (empty in PR 1) is iterable,
    and after `include_router`, FastAPI copies those routes (currently zero)
    into `app.routes`. Without `include_router` the test is no weaker than
    the import test below — but with it, future PRs that add routes to the
    desktop_browser router will see them appear in `app.routes`, which gives
    us a regression marker for "someone deleted the include_router call".

    For PR 1 (empty router), we assert that the router instance has a
    `.routes` attribute (FastAPI APIRouter contract) and that the app
    accepts our import without error — combined with the fact that PR 2+
    will add routes that will then appear in app.routes, this gives a
    real upgrade path for the assertion.
    """
    from tinyagentos.routes.desktop_browser import router as browser_router

    # APIRouter contract: must have a .routes attribute that is a list
    assert hasattr(browser_router, "routes")
    assert isinstance(browser_router.routes, list)

    # The app must have been built without error — the fixture proves that.
    # When PR 2 adds a route to browser_router, the assertion below will
    # become non-vacuous because every router-route will be reflected in
    # app.routes after include_router runs.
    browser_route_paths = {
        getattr(r, "path", None) for r in browser_router.routes
    }
    app_route_paths = {getattr(r, "path", None) for r in app.routes}

    # In PR 1 both sets are likely empty for browser_router, so the
    # assertion is trivially true. In PR 2+ this will catch deletion
    # of the include_router call.
    assert browser_route_paths.issubset(app_route_paths)


def test_desktop_browser_module_importable():
    """Defensive import test — catches packaging mistakes early."""
    from tinyagentos.routes.desktop_browser import router
    from tinyagentos.routes.desktop_browser.crypto import derive_cookie_key
    from tinyagentos.routes.desktop_browser.schema import (
        BROWSER_SCHEMA,
        COOKIE_SCHEMA,
    )
    from tinyagentos.routes.desktop_browser.store import (
        BrowserCookieStore,
        BrowserStore,
    )

    assert all([
        router,
        derive_cookie_key,
        BROWSER_SCHEMA,
        COOKIE_SCHEMA,
        BrowserCookieStore,
        BrowserStore,
    ])
