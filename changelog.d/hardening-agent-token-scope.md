### Security

- A deployed agent's `TAOS_LOCAL_TOKEN` now authenticates as that agent, acting
  for its registry owner, and is never admin (`via == "agent_token"`). It no
  longer passes `require_admin` or the settings gate, and no longer maps to the
  primary user, so a member's agent is no longer the machine's admin. The
  host-user local token used by scripts, `taosctl` and the kiosk is unchanged.
- Agent tokens are fenced to their own agent on the OpenClaw bootstrap, events
  and reply routes and on delegation, and read only the chat channels the agent
  belongs to. The `code_exec` skill refuses agent tokens.
- Crystallized sessions now carry the session's agent into the knowledge-graph
  write instead of the anonymous "system" subject.
- App-join and the cluster lease and task-report routes refuse per-agent tokens.
  A start-up step assigns agents with no registry owner to the install owner so
  their tokens can still post in chat. `code_exec` answers 403 to agent tokens.
