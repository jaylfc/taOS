### Fixed

- Auth: exempt-GET bearer log no longer writes a traceback for malformed
  tokens; it logs one line with the exception class name instead (fixes log
  flood on unauthenticated exempt GETs). Exempt-GET bound-agent local-token
  requests now write the same route record as the non-exempt path (fixes route
  log gap). Changelog entry for #3227 now correctly states the registry JWT
  promotion requires an active agent holding an unexpired `agents:read` grant.