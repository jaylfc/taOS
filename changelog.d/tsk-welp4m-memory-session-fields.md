### Fixed

- Every taOS memory writer now records session_id, turn_index and the turn's own
  session_date on the archive/memory record. turn_index is derived from the
  stored conversation count so it survives a restart instead of living in an
  in-memory dict, and the private-dict replay test is replaced by a real replay
  path test that re-ingests an existing conversation keeping original indexes
  and dates. Writers covered (with file:line):
  - chat post_message archive write — tinyagentos/routes/chat.py:559
  - chat save_chunk / user-memory capture — tinyagentos/routes/chat.py:136
  - chat websocket memory capture — tinyagentos/routes/chat.py:274
  - chat replay path — tinyagentos/routes/chat.py:102
  - bridge_session archive writes — tinyagentos/bridge_session.py:178
    (_archive_session_fields; record calls at lines 525, 642, 673, 729, 765)
  - channel_hub/router inbound + outbound archive — tinyagentos/channel_hub/router.py:99
  - agent_deploy smoke check — tinyagentos/routes/agent_deploy.py:290
  - user_workspace file capture save_chunk — tinyagentos/routes/user_workspace.py:28
