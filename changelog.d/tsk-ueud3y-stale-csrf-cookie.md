### Fixed

- A stale `taos_session` cookie from a previous install no longer forces CSRF
  enforcement on first-run setup or any other exempt route. The cookie is
  cleared in the response, and CSRF failures now return `{"error": ...}`
  instead of FastAPI's default `{"detail": ...}` so the SPA can render a
  friendly recoverable message.
