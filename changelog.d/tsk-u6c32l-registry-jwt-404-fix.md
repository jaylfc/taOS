### Fixed

- Auth middleware: a non-device Bearer on a route that is NOT on the closed
  allowlist and matches no registered route now has its registry JWT
  validated, and a valid token returns 404 instead of 401. The allowlist is
  still checked first and stays closed (no skeleton key); known
  non-allowlisted routes still return 401. Anti-enumeration for absent or
  invalid credentials is unchanged.
