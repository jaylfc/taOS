### Fixed

- Fixed `test_v2_handler_module_is_bus_view` to assert on the flattened `APIRoute` list instead of a non-existent `APIRouter` instance
- Fixed `test_v2_channels_served_by_bus_view` to stub the bus and assert unconditionally on the `unified_bus` field instead of skipping when no channels are pre-seeded
- Fixed `test_v2_channel_messages_proxies_to_bus` to seed the bus response without `unified_bus` and assert the route added it
- Removed the dead `CHAT_UNIFIED_BUS_ENABLED` kill switch and `_require_unified_bus` which could never be disabled
