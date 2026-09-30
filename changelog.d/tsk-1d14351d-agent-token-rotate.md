### Added

- `POST /api/agents/registry/{canonical_id}/rotate-tokens` now replaces the
  credential ON the same canonical identity and returns the new token in the
  response, instead of only bumping `token_min_iat`. The cutoff moves to
  `max(now + 1, current_cutoff + 1)` and the replacement is minted at the
  resulting cutoff, so a token minted in the same second is superseded rather
  than surviving its own rotation. Owner or admin may rotate an identity they
  own; an agent may rotate its OWN identity with its own live registry JWT (the
  route joins the middleware's Bearer allowlist), giving agents the
  self-service "rotate my credential" action they lacked. Mint responses
  (register, mint-internal, auth-request poll, rotate) now carry a
  `storage_guidance` field: store the token in two migration-surviving
  locations, mode 0600, outside git. (taOS #2158)

### Fixed

- `rotate_native_agent_token` used `now` as the rotation cutoff, so rotating in
  the second right after a mint left the previous token unsuperseded. It now
  uses `max(now + 1, current_cutoff + 1)` and mints the replacement at the
  resulting cutoff.
- `AgentRegistryStore.bump_token_min_iat` now advances the cutoff in one
  statement (`MAX(token_min_iat + 1, ?)`) instead of writing a value computed
  from an earlier read, so two rotations can never share a cutoff and a
  replacement can never be born superseded by a concurrent rotation.
