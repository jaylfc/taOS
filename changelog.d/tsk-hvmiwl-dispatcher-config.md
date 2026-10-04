### Added

- Per-user dispatcher configuration store (`DispatcherStore`) with GET/PUT `/api/dispatcher/config` endpoints. The dispatcher is disabled by default (enabled=false, cap fixed at 1 per the one-active-claim rule). Only session-authenticated users can access their own config; admins may manage any user's config via `?user_id=`. Agents are refused: an agent registry JWT alone gets 401 (the route is not on the agent-token allowlist), and a deployer-minted per-agent local token gets 403.
