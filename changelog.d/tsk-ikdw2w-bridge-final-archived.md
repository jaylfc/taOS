### Fixed

- Added archive recording for final bridge session events to ensure the final reply text reaches the taOSmd archive exactly once, matching the behavior of other event types (tool_call, tool_result, error, reasoning). This fixes the bug where final reply text was not being archived, preventing potential loss of critical conversation data.