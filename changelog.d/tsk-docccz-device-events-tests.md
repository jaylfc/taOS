### Fixed
- Device events: removed dead demo filter from poll loop
- Device events: replaced `_collect_from_stream` with deterministic `_read_until_ping` in tests
- Device events: rewritten `test_stream_stops_demo_after_switch_off` to use deterministic clock and stream reading
- Device events: rewritten `test_stream_upsert_on_avatar_change` to use single generator and deterministic reading
- Device events: added HTTP route tests for bearer auth and device scope validation