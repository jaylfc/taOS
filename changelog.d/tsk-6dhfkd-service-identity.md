### Added

- **Controller service identity (tsk-6dhfkd):** the controller registers a
  service principal (`service:controller`), owned by the install owner, at first
  boot. Its token (`principal_type` `service`) carries a space-separated `scope`
  claim (`records:write`) and an `exp` claim, is written atomically to
  `<data_dir>/.secrets/taos_controller_token` (mode 0600, never an environment
  variable, never logged) and is renewed by the controller before it expires, at
  start-up and by a background loop; a failed renewal keeps the old token until
  it expires and never blocks start-up. A suspended or revoked controller row is
  left alone rather than duplicated. The grant vocabulary gains `records:write`,
  `records:read`, `blobs:write`, `blobs:read`, `bus:send`, `bus:read`,
  `bus:inbox`, `secrets:reveal` and `archive:admin`; `bus:send` is stored as the
  existing `a2a_send` grant. `controller_record()` is the one helper that adds an
  `on_behalf_of` field to a record the controller writes for a person. Nothing in
  the controller writes records to the memory service yet, so no record is
  attributed to the controller by this change.

### Security

- A service token is refused wherever an agent or person token is expected
  (agent scope checks, identity proof, project-grant enumeration, self-rotation,
  and the middleware's exempt-GET, native-agent and deployed-agent paths), and no
  route mints or rotates one: rotating or adopting the controller's row over
  HTTP is refused, and an agent-kind token naming that row is not accepted.
