### Fixed

- Agent DM classification now includes archived agents in the roster: `agentNames` is built from both `liveAgents` and `archivedAgents` (original name, display name and archived slug). When both are empty, `agentNames` is `undefined` so MobileChatList keeps its back-compat path (all DMs are agents).
