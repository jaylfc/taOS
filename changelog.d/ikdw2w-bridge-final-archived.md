### Fixed

- Final reply text is now archived in bridge_session.py exactly once (previously it was only archived for tool_call/tool_result/error/reasoning events)
- The final reply text is byte-identical and not duplicated via chat.py conversation archives
- Individual delta fragments are not archived; only the concatenated final text is archived