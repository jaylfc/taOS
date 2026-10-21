### Fixed

- Friend-accept no longer blocks on peer delivery: the contact and peer link are created before the response returns, and only the network handshake exchange (and storing the peer's token) runs in the background with a 30s timeout.
