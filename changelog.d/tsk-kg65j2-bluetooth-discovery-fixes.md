### Fixed
- Bluetooth discovery auto-stop no longer raises AttributeError on D-Bus error replies (Message.MessageType -> MessageType)
- System bus connection now has 5s timeout, preventing hung bus from wedging all Bluetooth requests
- Explicit stop discovery (on=false) now checks StopDiscovery reply and returns 502 on unexpected D-Bus errors
- All D-Bus calls in discovery path bounded with 10s timeout, returning 504 on timeout
- StartDiscovery error now preserves previous auto-stop timer instead of canceling it
- Duplicate /api/hardware/bluetooth/device section removed from docs/hardware-routes.md