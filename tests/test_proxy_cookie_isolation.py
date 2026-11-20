"""Every proxy must deny EVERY cookie taOS itself issues, not just taos_session.

The defect this file exists to prevent: four independent proxies each carried
their own hand-written list of cookies to strip, and between them those lists
named exactly two of the five cookies taOS issues. So a real signed-in browser
relayed this origin's ``csrf_token`` -- plus ``taos_browser``, an httponly
session id bound to a user_id -- to taos.my and to *untrusted container app*
backends on every proxied request.

``csrf_token`` is the sharp one. ``middleware/csrf.py`` sets it
``httponly=False`` on purpose (the SPA must read it), so it is a readable,
origin-wide secret whose ONLY job is proving same-origin. Handing it to an
upstream hands over exactly what satisfies ``verify_csrf``.

The old tests could not catch this: they asserted the relayed Cookie header was
empty while driving a test client that carried no ``csrf_token`` cookie at all
-- a caller that does not exist in production. A test whose client holds only
one of the cookies CANNOT fail on a leak of the other, which is how this stayed
invisible. Every test below therefore hands the proxy a header holding ALL of
them and asserts NONE survives.
"""

import ast
import pathlib

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

from tinyagentos import issued_cookies as _issued
from tinyagentos.issued_cookies import TAOS_ISSUED_COOKIES
from tinyagentos.routes import (
    account_proxy,
    service_proxy,
    shortcut_proxy,
    userspace_apps,
)

# A header shaped like the one a real signed-in browser sends: every cookie taOS
# issues, plus one that genuinely belongs upstream and MUST survive. The
# upstream cookie is the discriminator -- an implementation that simply dropped
# the Cookie header wholesale would pass a "nothing leaked" assertion while
# breaking every proxied login.
_UPSTREAM = "upstream_sess=keepme"
_BROWSER_HEADER = "; ".join(
    [f"{name}=leaked-{name}" for name in sorted(TAOS_ISSUED_COOKIES)] + [_UPSTREAM]
)


def _assert_no_taos_cookie_survives(relayed: str | None) -> None:
    """No taOS-issued cookie may appear in *relayed*, and upstream must remain."""
    assert relayed is not None, "proxy dropped the Cookie header entirely"
    for name in TAOS_ISSUED_COOKIES:
        assert name not in relayed, (
            f"{name} was relayed upstream: {relayed!r}. Every cookie taOS issues "
            f"is controller-scoped and must never leave this origin."
        )
    assert _UPSTREAM in relayed, (
        f"the upstream cookie was dropped: {relayed!r}. Stripping controller "
        f"cookies must not break cookies that legitimately belong upstream."
    )


def test_account_proxy_denies_every_taos_cookie():
    """account_proxy -> taos.my is an ALLOW-list: only ``taosmy_*`` cookies go
    upstream (prefix stripped), so no taOS-issued cookie can leak, whatever its
    name. The header below holds every taOS cookie plus a namespaced upstream
    one, which must be the only thing that survives."""
    header = _BROWSER_HEADER + "; taosmy_upstream_sess=keepme"
    assert account_proxy._upstream_cookie_header(header) == "upstream_sess=keepme"


def test_service_proxy_denies_every_taos_cookie():
    _assert_no_taos_cookie_survives(service_proxy._strip_taos_cookies(_BROWSER_HEADER))


def test_userspace_apps_denies_every_taos_cookie():
    """The worst of the four by the code's own word: the backend here is an
    'untrusted container-app backend'."""
    _assert_no_taos_cookie_survives(
        userspace_apps._strip_taos_session_cookie(_BROWSER_HEADER)
    )


def test_shortcut_proxy_denies_every_taos_cookie():
    filtered = shortcut_proxy._filter_proxy_headers({"Cookie": _BROWSER_HEADER})
    _assert_no_taos_cookie_survives(filtered.get("Cookie"))


@pytest.mark.parametrize(
    "strip",
    [
        pytest.param(service_proxy._strip_taos_cookies, id="service_proxy"),
        pytest.param(userspace_apps._strip_taos_session_cookie, id="userspace_apps"),
    ],
)
def test_taos_only_header_relays_nothing(strip):
    """When the browser holds ONLY taOS cookies, the proxy must send no Cookie
    header at all rather than an empty or comma-noise one."""
    only_taos = "; ".join(f"{n}=x" for n in sorted(TAOS_ISSUED_COOKIES))
    assert not strip(only_taos)


def test_every_proxy_shares_one_deny_list():
    """Four hand-written lists is what let them drift apart in the first place.

    This asserts identity with the shared set, not mere equality of contents, so
    a future edit that re-forks a local copy fails here even if it happens to
    start with the same names.
    """
    for module, attr in [
        (service_proxy, "_STRIPPED_COOKIES"),
        (userspace_apps, "_PROXY_STRIPPED_COOKIES"),
        (shortcut_proxy, "_STRIPPED_PROXY_COOKIES"),
    ]:
        assert getattr(module, attr) is TAOS_ISSUED_COOKIES, (
            f"{module.__name__}.{attr} is a private copy, not the shared "
            f"TAOS_ISSUED_COOKIES set -- that is how the lists drifted apart."
        )


def _cookie_names_the_code_issues(pkg: pathlib.Path) -> set[str]:
    """Every cookie name passed to a ``set_cookie`` call under *pkg*.

    Parsed with ``ast`` rather than matched with a regex because the name is
    not always a literal at the call site. ``middleware/csrf.py`` holds it in a
    module constant::

        _COOKIE_NAME = "csrf_token"      # line 36
        response.set_cookie(_COOKIE_NAME, token, ...)   # line 94

    A literal-only scanner sees four of the five cookies taOS issues and misses
    exactly ``csrf_token`` -- the one this whole module exists for. It would
    have reported a clean tree while a rename of ``_COOKIE_NAME`` leaked the
    new cookie through all four proxies. So module-level ``NAME = "literal"``
    assignments are resolved and the constant call shape is covered too.
    """
    names: set[str] = set()
    for path in pkg.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # pragma: no cover - the package parses
            continue

        consts: dict[str, str] = {}
        for node in tree.body:
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            value = getattr(node, "value", None)
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = value.value

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called != "set_cookie":
                continue
            # Both call shapes in the tree: positional name, or key="name".
            arg: ast.expr | None = node.args[0] if node.args else None
            if arg is None:
                arg = next((kw.value for kw in node.keywords if kw.arg == "key"), None)
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.add(arg.value)
            elif isinstance(arg, ast.Name) and arg.id in consts:
                names.add(consts[arg.id])
    return names


def test_deny_list_covers_every_cookie_the_code_issues():
    """Drift guard: a cookie added tomorrow must not silently start leaking.

    Scans every ``set_cookie`` call in the package for the name it issues and
    asserts the deny-list already covers it. This is the check whose absence
    let csrf_token, taos_browser and taos_cs leak -- each was added long after
    the four strip lists were written, and nothing forced anyone back here.
    """
    pkg = pathlib.Path(__file__).resolve().parent.parent / "tinyagentos"
    found = _cookie_names_the_code_issues(pkg)

    # The scanner must be able to SEE something, or an empty result would make
    # this test vacuously green -- the exact failure mode this suite is about.
    # Both shapes are named on purpose: taos_session is a literal at its call
    # site, csrf_token is a module constant. Asserting only the literal one
    # would pass identically with constant resolution removed, so it could not
    # fail on the defect that resolution exists to fix.
    for shape, name in [("literal", "taos_session"), ("module constant", "csrf_token")]:
        assert name in found, (
            f"scanner found no {name} ({shape} call shape) among {sorted(found)}; "
            f"it is broken, not the tree. An empty or partial scan would pass "
            f"this test while proving nothing."
        )

    leaking = found - TAOS_ISSUED_COOKIES
    assert not leaking, (
        f"these cookies are issued by taOS but absent from TAOS_ISSUED_COOKIES, "
        f"so every proxy will relay them upstream: {sorted(leaking)}"
    )


def test_the_scanner_resolves_a_name_held_in_a_constant(tmp_path):
    """The scanner must cover the call shape ``csrf_token`` actually uses.

    The sibling test above runs against the real tree, where a regression in
    constant resolution shows up as a missing name. This one pins the
    behaviour in isolation: a literal-only scanner returns just the literal
    cookie here and fails, which is precisely how ``csrf_token`` stayed
    invisible to this guard.
    """
    (tmp_path / "constant_named.py").write_text(
        '_COOKIE_NAME = "held_in_a_constant"\n'
        "def f(response, token):\n"
        "    response.set_cookie(_COOKIE_NAME, token, httponly=False)\n"
    )
    (tmp_path / "literal_named.py").write_text(
        "def g(response, token):\n"
        '    response.set_cookie("held_as_a_literal", token)\n'
    )
    (tmp_path / "kwarg_named.py").write_text(
        "def h(response, token):\n"
        '    response.set_cookie(key="held_as_a_kwarg", value=token)\n'
    )

    assert _cookie_names_the_code_issues(tmp_path) == {
        "held_in_a_constant",
        "held_as_a_literal",
        "held_as_a_kwarg",
    }


# ---------------------------------------------------------------------------
# The ``taosmy_`` account namespace is controller-owned too.
#
# The defect: account_proxy stores the taos.my session as ``taosmy_taos_session``
# on this origin. That name is NOT in TAOS_ISSUED_COOKIES, so the three
# app-facing proxies (whose backends are UNTRUSTED) forwarded the user's
# ACCOUNT session to every proxied backend (leak), and relayed a backend's
# ``Set-Cookie: taosmy_<x>`` to the browser, where account_proxy's allow-list
# would forward it to taos.my as the user's session (fixation).
#
# Every test below drives the REAL route through ASGI with a stubbed upstream,
# so it holds the same Cookie header a signed-in browser sends.
# ---------------------------------------------------------------------------


_FULL = (
    "taos_session=L; csrf_token=C; taosmy_taos_session=ACCT; "
    "taosmy_other=x; appcookie=keep"
)


class _Stub:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_upstream(captured: dict, set_cookies: list[str]):
    async def _send(req, **kwargs):
        captured["cookie"] = req.headers.get("cookie")
        headers = [("content-type", "text/plain")] + [("set-cookie", v) for v in set_cookies]
        return httpx.Response(200, headers=headers, content=b"ok")

    return _send


def _service_harness(monkeypatch, captured, set_cookies):
    app = FastAPI()
    app.include_router(service_proxy.router)

    async def _loc(app_id):
        return {"runtime_host": "127.0.0.1", "runtime_port": 1, "ui_path": "/"}

    async def _inst(app_id):
        return True

    app.state.installed_apps = _Stub(get_runtime_location=_loc, is_installed=_inst)
    monkeypatch.setattr(
        service_proxy._http_client, "send", _fake_upstream(captured, set_cookies)
    )
    return app, "/apps/x/p"


def _userspace_harness(monkeypatch, captured, set_cookies):
    app = FastAPI()
    app.include_router(userspace_apps.router)

    async def _get(app_id):
        return {
            "app_type": "container", "enabled": True, "container_host": "127.0.0.1",
            "container_port": 1, "permissions_granted": [], "provenance": "user-uploaded",
        }

    app.state.userspace_apps = _Stub(get=_get)
    monkeypatch.setattr(
        userspace_apps._container_proxy_client, "send", _fake_upstream(captured, set_cookies)
    )
    return app, "/api/userspace-apps/x/proxy/p"


def _shortcut_harness(monkeypatch, captured, set_cookies):
    app = FastAPI()
    shortcut = {"kind": "dashboard", "port": 1, "path": "/", "auth": {"type": "none"}}

    @app.get("/sc")
    async def _sc(request: Request):
        return await shortcut_proxy.proxy_dashboard("agent", shortcut, request)

    monkeypatch.setattr(shortcut_proxy, "_resolve_container_ip", lambda *a, **k: "10.0.0.5")
    monkeypatch.setattr(
        shortcut_proxy._proxy_client, "send", _fake_upstream(captured, set_cookies)
    )
    return app, "/sc"


_HARNESSES = [
    pytest.param(_service_harness, id="service_proxy"),
    pytest.param(_userspace_harness, id="userspace_apps"),
    pytest.param(_shortcut_harness, id="shortcut_proxy"),
]


async def _call(harness, monkeypatch, cookie=None, jar=None, set_cookies=()):
    captured: dict = {}
    app, url = harness(monkeypatch, captured, list(set_cookies))
    headers = {"Cookie": cookie} if cookie is not None else {}
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies=jar
    ) as c:
        resp = await c.get(url, headers=headers)
    assert resp.status_code == 200
    return captured.get("cookie"), resp



@pytest.mark.asyncio
@pytest.mark.parametrize("harness", _HARNESSES)
async def test_account_namespace_never_reaches_backend(harness, monkeypatch):
    relayed, _ = await _call(harness, monkeypatch, cookie=_FULL)
    assert relayed == "appcookie=keep"


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", _HARNESSES)
async def test_prefix_case_and_whitespace_variants_stripped_lookalikes_kept(
    harness, monkeypatch
):
    header = "TAOSMY_x=1;  taosmy_y=2 ; nottaosmy_x=3; taosmyx=4; Taosmy_Z=5; ok=6"
    relayed, _ = await _call(harness, monkeypatch, cookie=header)
    assert relayed == "nottaosmy_x=3; taosmyx=4; ok=6"


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", _HARNESSES)
@pytest.mark.parametrize(
    "header",
    [
        "bad==value; taosmy_taos_session=ACCT; keep=1; ;;",
        'a="x; taosmy_taos_session=ACCT"; keep=1',
        "\x01bad; taosmy_taos_session=ACCT; keep=1",
        "keep=1; =oops; taosmy_taos_session=ACCT",
    ],
)
async def test_malformed_cookie_header_cannot_smuggle_account_cookie(
    harness, monkeypatch, header
):
    relayed, _ = await _call(harness, monkeypatch, cookie=header)
    assert "taosmy_" not in (relayed or ""), relayed
    assert "ACCT" not in (relayed or ""), relayed


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", _HARNESSES)
async def test_signed_in_jar_does_not_expose_account_cookie(harness, monkeypatch):
    """End to end: a real account sign-in is stored by account_proxy as
    ``taosmy_taos_session`` in the browser jar; a call through each proxy must
    not expose it (nor the local session next to it)."""
    stored = account_proxy._rewrite_set_cookie(
        "taos_session=ACCT; Path=/; Domain=taos.my", secure_ok=False
    )
    name, _, val = stored.split(";")[0].partition("=")
    assert name == "taosmy_taos_session"
    jar = {name: val, "taos_session": "L", "appcookie": "keep"}
    relayed, _ = await _call(harness, monkeypatch, jar=jar)
    assert relayed == "appcookie=keep"


_EVIL = [
    "taosmy_taos_session=EVIL; Path=/",
    "TAOSMY_x=EVIL",
    "taos_session=EVIL",
    "csrf_token=EVIL; Path=/",
    "no-equals-sign",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", _HARNESSES)
async def test_backend_cannot_set_controller_owned_cookies(harness, monkeypatch):
    _, resp = await _call(
        harness, monkeypatch, set_cookies=_EVIL + ["appcookie=1; Path=/x", "b=2; HttpOnly"]
    )
    relayed = resp.headers.get_list("set-cookie")
    assert relayed == ["appcookie=1; Path=/x", "b=2; HttpOnly"]


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", _HARNESSES)
async def test_set_cookie_without_owned_names_relayed_unchanged(harness, monkeypatch):
    _, resp = await _call(
        harness, monkeypatch, set_cookies=["appcookie=1; Path=/x", "nottaosmy_x=2"]
    )
    assert resp.headers.get_list("set-cookie") == ["appcookie=1; Path=/x", "nottaosmy_x=2"]


def test_all_proxies_use_the_shared_predicate_not_a_private_list():
    """Single source of truth: the prefix lives in issued_cookies, and each
    proxy module imports the predicate and the shared filters from there."""
    assert account_proxy._UPSTREAM_COOKIE_PREFIX is _issued.UPSTREAM_ACCOUNT_COOKIE_PREFIX
    assert _issued.UPSTREAM_ACCOUNT_COOKIE_PREFIX == "taosmy_"
    root = pathlib.Path(__file__).resolve().parent.parent / "tinyagentos" / "routes"
    for mod in ("service_proxy", "shortcut_proxy", "userspace_apps"):
        src = (root / f"{mod}.py").read_text()
        assert "is_controller_owned_cookie" in src or "strip_controller_cookies" in src, mod
        assert "relay_set_cookies" in src, mod
        assert '"taosmy_"' not in src, f"{mod} keeps a private copy of the prefix"
    ap = (root / "account_proxy.py").read_text()
    assert '_UPSTREAM_COOKIE_PREFIX = "taosmy_"' not in ap


@pytest.mark.parametrize(
    "name,owned",
    [
        ("taos_session", True), ("CSRF_TOKEN", True), (" taosmy_x ", True),
        ("TAOSMY_x", True), ("taosmy_", True), ("nottaosmy_x", False),
        ("taosmyx", False), ("appcookie", False),
    ],
)
def test_predicate(name, owned):
    assert _issued.is_controller_owned_cookie(name) is owned
