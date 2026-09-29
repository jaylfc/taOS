### Fixed
- bridge_session.py:245 - Added warning log when delta/tool_result reply lacks trace_id to distinguish missing trace from unmatched trace
- The warning helps users diagnose when a reply is not streaming into its expected message
