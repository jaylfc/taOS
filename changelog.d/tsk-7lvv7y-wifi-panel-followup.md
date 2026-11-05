### Fixed

- Empty password on Enter: when joining a secured Wi-Fi network, pressing Enter with an empty password no longer sends a POST request (the Join submit button remains disabled). Shows "Enter the network password." error instead.
- Load after operations: `handleToggleWifi` and `handleDisconnect` now `await load()` to ensure the network list refreshes before clearing the toggling/disconnecting status.
- Keyboard accessibility: Show password and Cancel buttons no longer have `tabIndex={-1}`, making them reachable for keyboard users.