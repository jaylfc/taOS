### Fixed

- Every taOS memory writer (chat post_message archive and user-memory capture,
  bridge_session archive writes, channel_hub/router, agent_deploy smoke check,
  user_workspace file capture) now records session_id, turn_index and the turn's
  own session_date on the archive/memory record. turn_index is derived from the
  stored conversation count so it survives a restart instead of living in an
  in-memory dict, and the private-dict replay test is replaced by a real replay
  path test that re-ingests an existing conversation keeping original indexes
  and dates.
