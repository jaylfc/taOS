### Added

- **Agent loop**: new `tinyagentos.agent_loop.AgentLoop` that lets the main
  taOS chat agent delegate heavy/long work to subagents while keeping its own
  loop free to present results and accept interrupts/redirects. Messages that
  arrive while the agent is working are queued (never dropped, never applied
  mid-step -- a turn is atomic) and surfaced at the next safe boundary. A
  redirect cancels in-flight subagent work at that boundary. `status()`
  reports what is running and what is queued for UI visibility
  (#tsk-rl2lfb).
