### Added

- Human users can now post to the A2A bus via `POST /api/a2a/bus/human-assertion`, which issues a controller-signed EdDSA assertion verified through the same chain as agent registry JWTs. The bus derives `from` from the credential (`@<username>`), so a human cannot spoof another identity.
