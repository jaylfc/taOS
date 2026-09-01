### Security

- Skill-exec agent identity now uses per-agent local tokens minted at deploy
  time instead of the shared host token. Each deployed agent receives its own
  distinct `TAOS_LOCAL_TOKEN` bound to its name, so concurrent deploys can no
  longer overwrite each other's credential binding. The shared host local token
  remains valid for admin/system callers but is no longer bound to any agent
  name, so an unbound local-token caller falls back to the body-asserted
  behaviour (or is denied where the credential mismatch check applies).
  `request.state.agent_name` set by the auth middleware is reliable for callers
  presenting a minted per-agent token; the shared host token and agents
  deployed before this change (until their next redeploy) remain unbound.
