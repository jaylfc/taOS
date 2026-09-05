### Fixed
- The consent approve surface no longer keeps its own copy of the "needs a project" scope list. `GET /api/agents/scope-vocabulary` now publishes the server's grantable scopes and the subset that must be bound to a project, and `ConsentActions` renders the project picker from that response, so a scope added server-side can no longer silently reintroduce the unfixable 400 on Approve.
- If that vocabulary cannot be loaded, Allow is disabled and the failure is shown, instead of falling back to a stale local list and approving with the wrong shape.
