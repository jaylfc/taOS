### Added

- **Controller service identity (tsk-6dhfkd):** the controller registers a
  service principal (`service:controller`), owned by the install owner, at first
  boot. Its token is a registry JWT (EdDSA, `iss` "taos-registry") with `sub` the
  canonical_id, integer `iat` and `exp` (24 hour lifetime), `jti` and `kind`
  "service" (the claim the memory service reads), plus `principal_type`
  "service" (the claim taOS's own verifiers read); no `aud`, no scope claim. The
  token is written atomically to `<data_dir>/.secrets/taos_controller_token`
  (mode 0600, never an environment variable, never logged) and renewed by the
  controller at two thirds of its life, at start-up and by a background loop; a
  failed renewal keeps the old token and never blocks start-up. Authority is the
  grants feed: the identity holds one grant record, `records:write`, created with
  the identity and never re-asserted, so revoking it sticks. A suspended or
  revoked controller row is left alone rather than duplicated. The grant
  vocabulary gains `records:write`, `records:read`, `blobs:write`, `blobs:read`,
  `bus:read`, `bus:inbox`, `secrets:reveal` and `archive:admin`; bus sending stays
  the existing `a2a_send` grant. `controller_record()` builds the
  `on_behalf_of` envelope field, `{"kind": "person" | "agent", "id": ...}`, and
  refuses anything else. Nothing in the controller writes records to the memory
  service yet, so no record is attributed to the controller by this change.

### Security

- A service token is refused wherever an agent or person token is expected
  (agent scope checks, identity proof, project-grant enumeration, self-rotation,
  and the middleware's exempt-GET, native-agent and deployed-agent paths), and no
  route mints or rotates one: rotating or adopting the controller's row over
  HTTP is refused, and an agent-kind token naming that row is not accepted.
