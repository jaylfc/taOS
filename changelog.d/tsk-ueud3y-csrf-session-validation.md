### Fixed

- Invalid/stale `taos_session` cookies are now validated against the session store and treated as absent for CSRF protection purposes. This prevents stale cookies from blocking first-run setup and other credential-establishing routes.

### Added

- CSRF protection now properly validates `taos_session` cookies before applying double-submit checks, ensuring a valid session exists for protected endpoints.
- The SPA will now render a user-friendly recoverable error page for CSRF failures instead of showing raw JSON error details, improving the user experience for developers who encounter CSRF protection issues.
