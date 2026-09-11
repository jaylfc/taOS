### Changed

- The authenticated A2A bus send proxy (`POST /api/a2a/bus/send`) now presents
  the caller's registry JWT to the bus and attributes an agent's message to its
  registry canonical_id (the token's `sub`) instead of its display handle.
  Both halves are required for bus-side verification: the bus authorises a
  sender by verifying the token signature against the registry public key and
  then requiring `token sub == from`, so a handle-spelled `from` could never be
  verified and a credential the bus never received was indistinguishable from
  none. Admin and human-assertion sends are unchanged (no credential is
  forwarded for either).
