### Fixed
- Auth middleware now checks registry record status and token rotation cutoff before returning 404 for an unlisted route, so revoked or rotated registry JWTs receive 401 instead of being misreported as a wrong URL.
