### Fixed
- A stale non-device `Authorization: Bearer ...` header no longer returns 401 before the session cookie is consulted; authenticated browser requests with a leftover registry token now reach the route as `via="session"`. The deferred 401 still fires when no `taos_session` and no valid credential authenticate the request.
