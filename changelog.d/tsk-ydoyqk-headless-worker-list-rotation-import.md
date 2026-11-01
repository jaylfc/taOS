### Fixed

- Cluster: the exempt-GET registry-JWT promotion in `auth_middleware.py` raised
  `UnboundLocalError` after dev merged in the deployed-agent runtime branch.
  That branch's function-local `from tinyagentos.agent_token_auth import
  (verify_registry_token, _enforce_rotation_cutoff)` made `_enforce_rotation_cutoff`
  local to the whole dispatch method, so the earlier exempt-GET call to it
  failed, logged `exempt-GET bearer: registry JWT validation failed`, and fell
  through to the anonymous view -- production got the redacted worker list with
  no error. The local imports now import only `verify_registry_token`; the
  module-level `_enforce_rotation_cutoff` is the single source for every call.
  The promotion also requires an active `agents:read` grant, so a registry
  JWT minted by deploy for a DEPLOYED agent (owner user_id + `memory_read`
  only) does not get `headless_full_view`; without the grant it falls through
  to the anonymous view, never a 403 (#3790, #4027).
- Auth: exempt-GET bearer log no longer writes a traceback for malformed
  tokens; it logs one line with the exception class name instead (fixes log
  flood on unauthenticated exempt GETs). Exempt-GET bound-agent local-token
  requests now write the same route record as the non-exempt path (fixes route
  log gap).