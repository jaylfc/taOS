### Added

- Scope agent memory recall per conversation audience: in DMs, only the participating person's conversations with the agent plus the agent's own non-personal knowledge are recalled; in channels, only memory from that channel (and shared project memory members can read) is recalled. Memory writes now include `turn_owner` and `channel_type` fields for this scoping.
