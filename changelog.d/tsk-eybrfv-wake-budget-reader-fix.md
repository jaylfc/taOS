### Fixed
- Wake-budget read surfaces now resolve the per-agent/per-project key from the
  agent's held task first, so the charge is not lost the moment the agent claims
  its task and the task leaves the ready-tasks view.
