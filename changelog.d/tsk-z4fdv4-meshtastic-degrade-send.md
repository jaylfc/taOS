### Fixed
- Meshtastic (LoRa) send path now degrades rich replies via `_degrade` instead of bypassing it, so dropped-element notices reach the transport; the `[part N/M]` denominator is derived from the actual chunking and always equals the emitted part count.
