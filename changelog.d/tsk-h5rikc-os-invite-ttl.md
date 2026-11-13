### Fixed

- Added `ttl_secs` parameter to OS-level agent invites (\`/api/agents/invites\`), matching the existing behavior in project-level invites. The parameter validates that the value is between 60 seconds and 24 hours (86400 seconds), defaults to store default (1 hour) when omitted, and controls invite expiry time.