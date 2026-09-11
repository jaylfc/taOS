### Added

- Added unified chat bus VIEW routes in `tinyagentos/routes/chat_unified_bus_view.py` to read/write the BUS for every conversation shape (project groups, DMs, agent channels)
- Implemented thin VIEW routes that proxy to the A2A bus with message ID cursor pagination (not timestamps) to avoid the since-is-a-timestamp trap
- Messages app now renders project groups + DMs + agent channels from the bus with correct unread + pagination
- Existing controller chat endpoints continue to respond (backward compatibility maintained)
- DMs render through the SAME path as a group (no dm-specific branch) as requested
- Added ChatBusBridge in `tinyagentos/chat/unified_chat_bridge.py` for forwarding controller chat writes to the bus while maintaining backward compatibility
- Updated `tinyagentos/routes/__init__.py` to register the unified chat bus view router
