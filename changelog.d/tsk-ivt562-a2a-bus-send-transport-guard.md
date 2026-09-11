### Fixed

- The A2A bus send proxy now withholds the caller's registry JWT when the
  operator-configured bus URL (`TAOS_A2A_BUS_URL`) is a non-loopback `http://`
  destination. The credential is forwarded only over `https://` or loopback
  `http://` (`127.0.0.1`, `::1`, `localhost`). Operators with a remote `http://`
  bus can restore forwarding by setting
  `TAOS_A2A_BUS_ALLOW_INSECURE_CREDENTIAL` to any truthy value.
