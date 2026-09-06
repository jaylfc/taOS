### Fixed

- **Agent-as-a-Model conversation history ordering**: `_run_agent_turn` in
  `tinyagentos/routes/agent_model_api.py` emitted prior conversation segments
  out of source order because user turns were deferred until the next user
  message arrived, while system/assistant messages were appended immediately.
  This inverted every user/assistant pair (`[u1, a1, u2]` became `a1, u1, u2`,
  and the pattern compounded as history grew). The fix appends each message in
  source order in a single pass and identifies the last user message separately
  (via index) so it remains the final prompt without reordering the transcript
  (#2500, fix-forward tsk-o43fol).
