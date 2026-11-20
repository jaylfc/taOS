### Changed

- **GET /auth/me accepts the host local token as a bearer** -- native taOS apps
  (Qt) hold a bearer, not a browser cookie, so GET /auth/me now validates an
  ``Authorization: Bearer <token>`` when there is no valid session cookie and
  returns the same ``{"user": {...}}`` shape for the admin (first user) plus
  ``"via": "local_token"``. The cookie path is unchanged and wins when both are
  present; agent-bound tokens and registry JWTs are not a person and are refused
  with 401.
