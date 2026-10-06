### Fixed

- Bound agent deltas now broadcast on the message's stored channel when channel_id is omitted from the delta body, allowing bound agents to reach channel subscribers without specifying the channel again.

### Added

- Test to verify bound agent delta broadcasts on the message's channel when channel_id is omitted.

### Documentation

- Fixed agent coordination documentation to correctly describe routes that bind authors, rejecting thinking routes from binding/override, and clarifying delta/state routes check message ownership instead of overriding author fields.