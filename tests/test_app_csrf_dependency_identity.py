"""An ordinary test module runs against the REAL `verify_csrf`, by identity.

Card tsk-eioojd.  `tests/conftest.py` used to no-op `verify_csrf` for every
test file whose path did not contain the substring "test_csrf".  That was
inverted in #2547: the real dependency is now the default and opting out takes
an explicit opt-out marker.

`tests/test_app_csrf_wiring.py` already proves the behaviour end to end.  Its
identity guard, though, checks `__module__` and `__name__`, and any stub can
copy those two strings.  This module asserts at the granularity of the defect:
the callable that the module attribute holds, and the callable the BUILT app
actually runs on a protected route, is the function whose code lives in
`tinyagentos/middleware/csrf.py`, not a no-op defined anywhere else.

This file's name deliberately does NOT contain "test_csrf", so it sits outside
the old carve-out and goes red if that bypass ever comes back.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from tinyagentos.middleware import csrf

# POST /auth/logout carries `Depends(verify_csrf)` explicitly AND sits under a
# router registered with the router-wide `dependencies=_csrf` list.
PROTECTED_PATH = "/auth/logout"

_REAL_CSRF_FILE = Path(csrf.__file__).resolve()


def _defined_in(fn) -> Path | None:
    code = getattr(fn, "__code__", None)
    if code is None:
        return None
    return Path(code.co_filename).resolve()


def _all_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _all_dependency_calls(dep)


def test_module_attribute_is_the_real_verify_csrf():
    """The no-op must NOT be installed for an ordinary test file."""
    where = _defined_in(csrf.verify_csrf)

    assert where == _REAL_CSRF_FILE, (
        f"tinyagentos.middleware.csrf.verify_csrf is {csrf.verify_csrf!r}, "
        f"defined in {where}, not in {_REAL_CSRF_FILE}. This module is running "
        f"under a CSRF bypass."
    )


def test_built_app_protected_route_depends_on_the_real_verify_csrf(tmp_path):
    """What the app runs is decided at `include_router` time, so build it here."""
    from tinyagentos.app import create_app

    app = create_app(data_dir=tmp_path)
    routes = [
        r for r in app.routes
        if isinstance(r, APIRoute) and r.path == PROTECTED_PATH and "POST" in r.methods
    ]
    assert routes, f"no POST {PROTECTED_PATH} route on the built app"

    calls = list(_all_dependency_calls(routes[0].dependant))
    csrf_calls = [c for c in calls if getattr(c, "__name__", "").endswith("verify_csrf")]
    assert csrf_calls, f"POST {PROTECTED_PATH} has no verify_csrf dependency at all"

    for call in csrf_calls:
        where = _defined_in(call)
        assert where == _REAL_CSRF_FILE, (
            f"POST {PROTECTED_PATH} depends on {call!r}, defined in {where}, "
            f"not on the real verify_csrf in {_REAL_CSRF_FILE}."
        )
