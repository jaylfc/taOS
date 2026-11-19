### Fixed

- **Device pairing: a failed bell notification is now logged, not swallowed (#2485 item 13)** — the best-effort notification raised when a pair request is created still cannot fail the request, but a failure now logs a warning with the exception instead of disappearing into a bare `except Exception: pass`.
