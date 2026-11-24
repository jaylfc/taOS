### Fixed

- Agent name reservation across deploy, import, and restore create paths to prevent race conditions where concurrent operations could pick the same name, causing duplicate config entries and local token revocation issues.
- `reserve_agent_slug` now skips suffixed candidates that already exist in `config.agents` as well as reserved ones.
