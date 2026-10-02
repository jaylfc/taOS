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

### Security
- Added a red-first test (`tests/test_device_tls_listener.py`) covering
  cert persistence, embedded token TLS enforcement, fingerprint propagation,
  and MITM proxy fingerprint divergence.
