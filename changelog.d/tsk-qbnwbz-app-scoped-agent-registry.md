### Fixed

- Root the taosmd agent registry at each app's resolved data_dir so in-app
  consumers (the /api/agents/deploy route, the v2 persona startup migration)
  use app.state.taosmd_agent_registry instead of the process-global default.
  The existing app.state.agent_registry (AgentRegistryStore) is preserved so
  other in-app consumers are unaffected. A single AgentRegistry instance is
  shared between _tm_agents._default_registry and app.state.taosmd_agent_registry
  so CLI callers and in-app consumers never diverge.
