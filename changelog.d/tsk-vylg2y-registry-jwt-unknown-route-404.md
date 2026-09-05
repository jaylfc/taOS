### Fixed

- Auth middleware now returns 404 (not 401) for a valid registry JWT presented against a path that no route serves. This makes a wrong URL distinguishable from dead credentials while keeping the agent-token allowlist closed and preserving the anonymous 401. A route that exists but is off the allowlist is not a wrong URL, so it keeps its 401/403 from the session gate.
