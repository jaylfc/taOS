### Added

- WireClaw adapter (tinyagentos/orb/bridges/wireclaw.py):
  - BRIDGE_ID="wireclaw", TITLE="WireClaw", APP_ID="wireclaw"
  - installed() returns bool based on nats CLI availability (shutil.which("nats"))
  - evaluate() builds targets from LAN orbs running WireClaw with device URL instruction
  - Pure adapter with no network or discovery calls