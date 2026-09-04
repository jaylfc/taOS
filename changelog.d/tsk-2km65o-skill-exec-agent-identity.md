### Security

- Skill-exec agent identity is now derived from the presented local-token
  credential binding, not from the request body. A deployed agent passing
  another agent's handle in the body is rejected with 403 instead of being
  served. `_resolve_agent_workspace`, `_capture_tool_receipt`,
  `_check_execution_policy`, notes and todo tools all key off the same
  credential-bound `agent_name`.
