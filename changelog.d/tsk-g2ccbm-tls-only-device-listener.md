### Added
- TLS-only device listener on port 6974 (`TAOS_DEVICE_TLS_PORT`) with a
  self-signed certificate persisted under the data directory (0600). The
  controller generates the certificate on first boot and reuses it across
  restarts.
- Embedded-platform device bearer tokens are now refused on plain HTTP
  (port 6969) with `{"error": "device_tls_required"}`. Phones and other
  legacy platforms continue to work over plain HTTP unchanged.
- Pair-request creation response and Decision metadata now include
  `server_cert_fingerprint` (SHA-256 colon-hex of the controller's own
  certificate DER). Devices must pin the fingerprint from their own TLS
  handshake and abort with `pair_fingerprint_mismatch` on mismatch.

### Fixed
- TLS listener no longer re-runs the app lifespan (uses `lifespan="off"`),
  preventing duplicate startup of stores, schedulers, and background loops.
- TLS listener bind failure (e.g. port already in use) no longer crashes the
  controller; the failure is logged and the main server stays up.
- Private key and certificate are now created atomically with mode 0o600 from
  creation (using `os.open` with `O_CREAT|O_EXCL` and `os.replace`), eliminating
  the umask window and never swallowing permission errors.
- Silent TLS listener skip is eliminated: port collision with main/proxy port
  and missing cert/key are now logged at ERROR level so operators can diagnose
  why embedded devices cannot connect.
- Documentation: `push_token` max length corrected to 4096 (was 255);
  `pair_fingerprint_mismatch` removed from HTTP error table (it is a
  device-side abort, not a server response); Decision metadata description
  clarified.

### Security
- Added a red-first test (`tests/test_device_tls_listener.py`) covering
  cert persistence, embedded token TLS enforcement, fingerprint propagation,
  and MITM proxy fingerprint divergence.
- New red-first tests for lifespan isolation, bind-failure resilience, and
  atomic 0o600 key creation.
