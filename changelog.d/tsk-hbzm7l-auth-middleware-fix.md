### Fixed

- Auth middleware now validates credentials first: if a valid token is presented,
  the request falls through to routing and unknown paths return 404 instead of 401.
  If the credential is absent or invalid, 401 is returned uniformly (anti-enumeration
  property preserved for anonymous callers).