Additional fix: make the undeploy_agent function actually remove the trace dir when delete_state=True, matching the docstring.

While the current code removes agent workspaces and memory, the trace directory is also created by the deployer and should be cleaned up with the agent. The PRAGMA journal_mode=WAL is added directly in _SCHEMA (which is correct), and busy_timeout pragmas are added for consistency with other stores.

Also added an eviction mechanism to the SpanStoreRegistry to keep the registry size bounded with an LRU implementation.

### Fixed
- Fixed SpanStoreRegistry to use bounded LRU eviction
- Made undeploy_agent actually remove trace directory when delete_state=True
- Added busy_timeout pragma to browser_sessions.py

### Added
- WAL mode journal pragma to browser_sessions.py
- Trace directory removal in undeploy_agent