### Added
- POST /api/agents/deploy now passes the deploying session's user_id to agent_registry.register() for each deployed agent.
- A memory_read grant is added for the deployed agent's canonical_id in the agent_grants store.
- A scoped registry JWT (bound to the owner user_id and permissions come from the agent's grants) is minted and passed as TAOS_AGENT_TOKEN to the agent container via DeployRequest.agent_token.
- DeployRequest gains an optional agent_token field; when non-empty, TAOS_AGENT_TOKEN is set in the container env (additive; TAOS_LOCAL_TOKEN unchanged).