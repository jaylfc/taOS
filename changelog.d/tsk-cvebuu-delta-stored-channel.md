### Fixed

- Bound agent deltas now broadcast on the message's stored channel when channel_id is omitted from the delta body, allowing bound agents to reach channel subscribers without specifying the channel again.
- Restored `test_bound_agent_state_missing_message_is_403` (asserts POST /api/chat/messages/nonexistent/state with bound agent token returns 403).
- Removed conditional guard around broadcast payload assertions in `test_bound_agent_delta_without_channel_broadcasts_on_message_channel` so payload asserts always run.
- Fixed duplicate sentence in agent coordination documentation for chat author identity.

### Added

- Test to verify bound agent delta broadcasts on the message's channel when channel_id is omitted.

### Documentation

- Fixed agent coordination documentation to correctly describe routes that bind authors, rejecting thinking routes from binding/override, and clarifying delta/state routes check message ownership instead of overriding author fields.