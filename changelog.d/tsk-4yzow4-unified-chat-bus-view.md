### Added

- Added unified chat bus VIEW routes at `/api/chat/v2/...` in `tinyagentos/routes/chat_unified_bus_view.py` to read/write the A2A bus for every conversation shape (project groups, DMs, agent channels)
- Implemented thin VIEW routes that proxy to the A2A bus with message ID cursor pagination (not timestamps) to avoid the since-is-a-timestamp trap
- Existing controller chat endpoints continue to respond (backward compatibility maintained)
- DMs render through the SAME path as a group (no dm-specific branch) as requested
- Added ChatBusBridge in `tinyagentos/chat/unified_chat_bridge.py` for forwarding controller chat writes to the bus while maintaining backward compatibility

### Fixed

- Registered the unified chat bus view router in `tinyagentos/routes/__init__.py` so it is served by the app
- Moved unified bus view routes to `/api/chat/v2/...` to avoid path collisions with the live chat router at `/api/chat/...`
- Removed `ensure_bus_channel_exists` junk-message write from `ChatBusBridge` (bus threads are created on first post, no explicit init message needed)
- Moved `ChatBusBridge` from a module-level `_chat_bridge` global to `app.state.chat_bus_bridge` so each app instance gets its own bridge
- Bound the bus `from` field to the controller principal `"controller"` and carried the human `author_id` in the message body instead of forwarding it as `from`
