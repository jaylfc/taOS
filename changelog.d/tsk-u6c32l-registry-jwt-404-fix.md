### Fixed

- Auth middleware now authenticates valid registry JWT bearer tokens before
  checking the closed allowlist: unknown routes return 404 instead of 401,
  while known non-allowlisted routes still return 401 and the allowlist remains
  closed (no skeleton key). Anti-enumeration for absent or invalid credentials
  is unchanged.
