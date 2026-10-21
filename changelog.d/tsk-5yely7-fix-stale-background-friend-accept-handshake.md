### Fixed

- Prevent stale background friend-accept handshake from overwriting newer peer-link outbound token by adding expected_inbound_token check to set_outbound_token.