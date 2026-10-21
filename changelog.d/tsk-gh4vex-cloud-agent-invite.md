### Added

- New `cloud_agent` invite kind for relay-only connection bundles. Minting a `cloud_agent` invite requires `TAOS_CONTROLLER_RELAY_URL` to be set to a safe HTTPS URL; the redeemed bundle includes only the relay endpoint (no LAN or mesh), and the default check-in interval is 900 seconds. Token remains out of the bundle as before.
