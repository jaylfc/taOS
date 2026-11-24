### Fixed

- Friend-accept now reports when the directory/local friendship was accepted but the peer handshake was refused (the link stays revoked), instead of returning a generic 403. On an inbound peer handshake, a refused link write now restores the contact status to revoked so a revoked peer is not left active.
