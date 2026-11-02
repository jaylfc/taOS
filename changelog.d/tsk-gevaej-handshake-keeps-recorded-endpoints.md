### Fixed

- POST /api/peer/handshake no longer overwrites a live or revoked peer link's endpoints with values from a repeat handshake envelope; only the first handshake's endpoints (filtered to valid http/https URLs with hostnames) are stored, preventing SSRF allowance manipulation.