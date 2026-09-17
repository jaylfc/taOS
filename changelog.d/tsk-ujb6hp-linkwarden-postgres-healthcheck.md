### Fixed

- Add healthcheck and depends_on to companion services (e.g. postgres) to prevent race conditions on first boot. This ensures the app waits for companions to be ready before starting.
