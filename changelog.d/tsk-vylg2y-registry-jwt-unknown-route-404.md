### Fixed

- Auth middleware now returns 404 (not 401) for a valid registry JWT presented against a route that is not in the closed agent-token allowlist. This makes a wrong URL distinguishable from dead credentials while keeping the allowlist closed and preserving the anonymous 401.
