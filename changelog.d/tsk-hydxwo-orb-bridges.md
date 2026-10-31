### Added

- Orb bridges adapter contract (tinyagentos/orb/bridges/__init__.py):
  - BridgeResult dataclass with bridge_id, title, installed, targets, instruction and to_dict()
  - Pure helper functions: orbs_running, orb_host, target
  - Auto-discovery via pkgutil.iter_modules in adapters()
  - evaluate_all runs all adapters, logs errors per adapter, one failure does not hide others
- MimiClaw reference adapter (tinyagentos/orb/bridges/mimiclaw.py):
  - BRIDGE_ID="mimiclaw", TITLE="MimiClaw", APP_ID="mimiclaw", WS_PORT=18789
  - installed() returns None (no host-side tool)
  - evaluate() builds targets from LAN orbs running MimiClaw with WebSocket URL instruction