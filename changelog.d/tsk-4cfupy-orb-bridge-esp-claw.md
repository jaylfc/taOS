### Added

- ESP-Claw bridge adapter (tinyagentos/orb/bridges/esp_claw.py):
  - BRIDGE_ID "esp-claw", TITLE "ESP-Claw", APP_ID "esp-claw"
  - installed() returns None (configured through device web UI, no host tool)
  - evaluate() builds targets from LAN orbs running app "esp-claw" with non-None orb_host
  - Per-target instruction: "Open http://{host}/ in a browser to configure it" (upstream espressif/esp-claw 345073d serves / and /api/config)
  - Top-level instruction: "Open the device web UI shown for your Orb." / "No Orb running ESP-Claw found on the LAN."
  - Adapter auto-discovered via bridges.adapters(), pure (no network/subprocess/discovery calls)