### Fixed
- Fixed `test_chat_kilo_changed_prompt_starts_new_session` in `tests/test_taos_agent_chat.py`:
  - Corrected quote mismatch in `_KILO_STUB_CAPTURE` bash script (closing double quote to single quote)
  - Added error-frame assertions for both chat turns to verify successful turns carry no error frames
  - Simplified vacuous assertion for unchanged-prompt resume case