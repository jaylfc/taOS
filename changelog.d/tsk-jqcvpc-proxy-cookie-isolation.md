### Fixed
- All four proxies (`routes/account_proxy.py`, `routes/service_proxy.py`,
  `routes/userspace_apps.py`, `routes/shortcut_proxy.py`) now strip every cookie
  taOS itself issues before relaying a request upstream, instead of each
  carrying its own hand-written strip list. Between them those four lists named
  two of the five cookies this origin sets, so `csrf_token`, `taos_browser` (an
  httponly session id bound to a `user_id`) and `taos_cs` were relayed to
  taos.my, to container app backends and to shortcut targets. `csrf_token` is
  deliberately `httponly=False` so the SPA can read it, which makes it a
  readable origin-wide secret whose only job is proving same-origin — relaying
  it handed an upstream exactly what satisfies `verify_csrf`. The deny-list is
  now a single shared `TAOS_ISSUED_COOKIES` frozenset in
  `tinyagentos/issued_cookies.py`, and `tests/test_proxy_cookie_isolation.py`
  asserts both that all four proxies share it by identity and that it covers
  every `set_cookie` call in the package (#tsk-jqcvpc).

### Changed
- CSRF is enforced for real in the test suite. The autouse fixture that replaced
  `verify_csrf` with a no-op for every test file whose path lacked the substring
  `test_csrf` is gone; opting out is now the explicit `@pytest.mark.csrf_bypass`
  marker, which nothing uses and which `tests/test_csrf_bypass_debt.py` asserts
  stays unused. The shared `client` fixture echoes the `csrf_token` cookie into
  `X-CSRF-Token` on mutating requests the way the SPA's `taosFetch` does, so
  tests satisfy the real check rather than switching it off. Because the old
  carve-out matched on filename, renaming a test file silently re-armed the
  bypass; that is no longer possible (#tsk-jqcvpc).
