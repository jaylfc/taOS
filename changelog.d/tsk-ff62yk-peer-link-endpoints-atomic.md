### Fixed

- Fix the overlapping first-accept race in the peer handshake: hub friend-accept and the peer handshake route now pass keep_existing_endpoints=True unconditionally when establishing a peer link, and the store conflict arm keeps non-empty stored endpoints (taking the new ones only when the stored value is NULL, '' or '[]'), so a concurrent second accept can no longer overwrite the first link's endpoints.
