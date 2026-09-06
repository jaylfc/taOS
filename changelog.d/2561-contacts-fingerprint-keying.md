### Fixed
- Cross-user contacts are now keyed on the peer's ed25519 signing-key
  fingerprint rather than on their username, so a peer who changes or reuses a
  username can no longer be confused with an existing pinned contact. Stores
  created before this change are upgraded in place on first open: existing rows
  have their `peer_fingerprint` backfilled from the stored public key, and rows
  whose key is missing or malformed are left unkeyed instead of aborting the
  upgrade. Revocation now reports how many contacts matched and cascades to
  every contact sharing the revoked fingerprint, and blocking a peer cascades
  consistently through both block paths (#2561).
