### Fixed

- Stale `taos_session` cookies from a previous install no longer lock users out of
  first-run setup with a 403. `verify_csrf` now validates the session behind the
  cookie: when the token does not resolve to a live session the cookie is treated
  as absent for CSRF purposes and cleared in the response. This fixes the
  reinstall bring-up state where a browser still presents the old cookie but has
  no matching `csrf_token`, which previously forced the double-submit check to
  fail on every sign-in surface.
