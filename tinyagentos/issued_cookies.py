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
