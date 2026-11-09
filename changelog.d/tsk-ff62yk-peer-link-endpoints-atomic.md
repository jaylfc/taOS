### Fixed

- Added `keep_existing_endpoints` parameter to `establish_peer_link`: when True, the ON CONFLICT DO UPDATE arm keeps stored endpoints (`endpoints = peer_links.endpoints`) instead of overwriting with `excluded.endpoints`. Passed `True` from `hub.py:_try_handshake` and `peer.py:peer_handshake` to prevent overlapping first accepts from overwriting each other's endpoints.