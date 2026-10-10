### Removed
- The inert ChatBusBridge (`tinyagentos/chat/unified_chat_bridge.py`) and its app.state.chat_bus_bridge attribute have been removed as it was unused and gated behind the unified-chat-transport epic until ACLs enforce. The read-only bus view routes remain intact.
