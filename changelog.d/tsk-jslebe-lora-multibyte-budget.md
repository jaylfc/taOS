### Fixed
- LoRa 237-byte wire budget now chunks on UTF-8 codepoint boundaries instead of slicing bytes, fixing corruption and over-budget frames for non-ASCII (CJK, emoji) messages; the Meshtastic connector guard now raises instead of narrate-truncating-and-shipping oversize frames, and the `meshtastic` platform is reachable via `POST /api/channel-hub/connect`.
