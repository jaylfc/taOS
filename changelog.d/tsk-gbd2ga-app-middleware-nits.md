### Fixed

- Security headers now include `Referrer-Policy: no-referrer` and a restrictive
  `Permissions-Policy` on every response, closing the unauthenticated header gap
  found in the September 2026 security audit (S2-31).
- `X-Taos-Version` is coarsened to `taOS` for unauthenticated callers; the full
  build version is only sent to requests that presented a credential (S2-32).
- GZip middleware is now added inside the CSRF cookie-setting layer so response
  bodies carrying `Set-Cookie` headers are never compressed (BREACH precondition).
- The `/setup` startup-exempt prefix is anchored as `/setup/` so `/setupfoo` is
  no longer matched as a setup path.
- `gui()` now checks for the SPA bundle and exits 503 with a build hint when
  `static/desktop/index.html` is missing, instead of opening a browser to a
  500/404.
- `GET /manifest` without `?app=` now returns a plain 400 instead of leaking
  the FastAPI 422 validation schema (S2-33).
- The uvicorn `Server` banner is suppressed via `server_header=False` (S2-32).
