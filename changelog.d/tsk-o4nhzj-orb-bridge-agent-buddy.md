### Added

- Agent Buddy bridge adapter (tinyagentos/orb/bridges/agent_buddy.py):
  - BRIDGE_ID="agent-buddy", TITLE="Agent Buddy", APP_ID="agent-buddy"
  - installed() returns None (upstream runs daemon via npm, no binary on PATH)
  - evaluate() builds targets from Agent Buddy probe replies with npm pair command instruction
  - Skips replies with empty address, top-level instruction when no targets found
  - Auto-discovered by bridges registry, included in evaluate_all()