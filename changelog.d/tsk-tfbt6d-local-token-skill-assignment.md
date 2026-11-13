### Fixed

- Bound per-agent local tokens are now fenced by skill assignment, the same
  guard as the registry-JWT deployed-agent path: a deployed agent presenting its
  bound `TAOS_LOCAL_TOKEN` can only execute skills assigned to it
  (`execute_skill` now checks `via == "local_token"` together with
  `request.state.agent_name`). The unbound host/CLI local token, admin
  sessions, and `registry_jwt_native_agent` remain unrestricted.