### Added

- `POST /api/a2a/bus/send` now returns `credential_forwarded: bool` in the
  response body, telling the caller whether its registry credential was attached
  to the bus request. The field is `true` when the header was forwarded over
  `https://` or loopback `http://`, or when the opt-in
  `TAOS_A2A_BUS_ALLOW_INSECURE_CREDENTIAL` is set, and `false` otherwise.
