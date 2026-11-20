"""The single source of truth for cookies taOS itself issues on this origin.

Every one of these is controller-scoped: it is meaningful only to this host and
must never be relayed to an upstream by any proxy. They are collected here, in
one importable set, because the alternative was tried and failed -- four proxies
(``account_proxy``, ``service_proxy``, ``userspace_apps``, ``shortcut_proxy``)
each carried a private hand-written list, and between them those lists named
two of the five cookies below. The other three leaked.

Why a DENY-list for service_proxy, userspace_apps and shortcut_proxy: those
backends set and read cookies under names this repo cannot enumerate, so the
only workable rule is "strip what taOS itself issues". What this origin issues
is knowable and finite, and ``tests/test_proxy_cookie_isolation.py``
mechanically asserts this set covers every ``set_cookie`` call in the package.

``account_proxy`` does NOT use this set. It namespaces taos.my cookies under
``taosmy_`` and forwards only ``taosmy_*`` cookies upstream (an allow-list), so
a cookie this origin issues can never leak to taos.my even if it is missing
here. The namespace also fixes a name collision: taos.my's session cookie is
``taos_session``, the same name as ours.

The ``taosmy_`` namespace is controller-owned too: it holds the user's taos.my
ACCOUNT session. So the three app-facing proxies (service_proxy,
shortcut_proxy, userspace_apps) strip everything the controller owns, which is
the issued set PLUS the whole account namespace, via
``is_controller_owned_cookie``. Both directions:
  * request: the browser's ``taosmy_*`` cookies never reach an untrusted
    backend (a deny-list on the issued set alone leaked the account session);
  * response: a backend's ``Set-Cookie`` for an owned name is never relayed, or
    a backend could plant ``taosmy_<x>`` (its own taos.my session id, i.e.
    session fixation) or overwrite ``taos_session`` / ``csrf_token`` here.

ADDING A COOKIE? Add it here too. The drift guard in that test file will fail
until you do -- that is deliberate, and it is the check whose absence caused
this bug.
"""

TAOS_ISSUED_COOKIES = frozenset({
    # The local admin session credential. A taos.my log leak or a compromised
    # container would otherwise expose valid local admin session tokens.
    "taos_session",
    # The CSRF double-submit token. Set httponly=False ON PURPOSE so the SPA can
    # read it, which makes it a readable origin-wide secret whose only job is
    # proving same-origin. Relaying it hands an upstream exactly what satisfies
    # verify_csrf(). This is the one that motivated the sweep.
    "csrf_token",
    # Shortcut-proxy session credential.
    "taos_shortcut",
    # Browser-proxy session id, httponly, bound to a user_id -- a credential.
    "taos_browser",
    # Colour-scheme UI hint. Carries no security value, but it is ours and has
    # no business upstream. Denying everything we issue is a rule that can be
    # checked mechanically; "deny the sensitive ones" needs a judgement call at
    # every future call site, and that is what drifted.
    "taos_cs",
})

# The cookie namespace for taos.my (account service) cookies on this origin.
# account_proxy writes upstream cookies as ``taosmy_<name>`` and forwards only
# these upstream; the app-facing proxies must treat the whole namespace as
# controller-owned. One definition, imported by every user of it.
UPSTREAM_ACCOUNT_COOKIE_PREFIX = "taosmy_"


def is_controller_owned_cookie(name: str) -> bool:
    """True when *name* is a cookie the controller owns and untrusted
    backends must neither receive nor set: anything in TAOS_ISSUED_COOKIES or
    anything in the ``taosmy_`` account namespace. Case-insensitive and
    whitespace-tolerant on purpose (conservative: a look-alike is stripped, an
    unrelated name like ``nottaosmy_x`` or ``taosmyx`` is not matched)."""
    low = name.strip().lower()
    return low in TAOS_ISSUED_COOKIES or low.startswith(UPSTREAM_ACCOUNT_COOKIE_PREFIX)


def strip_controller_cookies(cookie_header: str) -> str:
    """Remove every controller-owned cookie from a Cookie header; keep the rest.

    Textual ';' split that does not depend on SimpleCookie's grammar, so a
    malformed header can never smuggle an owned cookie through.
    """
    if not cookie_header:
        return ""
    kept = []
    for part in cookie_header.split(";"):
        stripped = part.strip()
        if not stripped:
            continue
        if is_controller_owned_cookie(stripped.split("=", 1)[0]):
            continue
        kept.append(stripped)
    return "; ".join(kept)


def relay_set_cookies(response, upstream_headers) -> None:
    """Append the backend's ``Set-Cookie`` headers to *response*, except any
    whose cookie name the controller owns (or that has no ``name=``, which a
    browser would store as a nameless cookie). Every other Set-Cookie is
    relayed unchanged and as a separate header line (``dict(headers)`` would
    have merged them into one comma-joined, broken header).
    """
    items = (
        upstream_headers.multi_items()
        if hasattr(upstream_headers, "multi_items")
        else upstream_headers.items()
    )
    for key, value in items:
        if key.lower() != "set-cookie":
            continue
        name, eq, _ = value.split(";", 1)[0].partition("=")
        if not eq or not name.strip() or is_controller_owned_cookie(name):
            continue
        try:
            response.raw_headers.append((b"set-cookie", value.encode("latin-1")))
        except UnicodeEncodeError:
            continue
