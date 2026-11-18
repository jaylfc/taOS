### Fixed

- Pinned peer-link endpoints now recover when the first handshake stored an empty/invalid endpoint list. `_safe_endpoints` now rejects unparseable or out-of-range ports. Both `hub.py:_try_handshake` and `peer.py:peer_handshake` use the filtered stored endpoints when non-empty, otherwise fall back to the fresh advertised endpoints.