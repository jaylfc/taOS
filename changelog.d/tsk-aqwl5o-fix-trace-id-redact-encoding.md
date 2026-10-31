### Fixed
- history_import.claude_code: read trace_id from camelCase sessionId (fallback to session_id); redact tool_call args via new _redact_args helper; add encoding=utf-8 to file opens