### Fixed

- Agent name reservation across deploy, import, and restore create paths to prevent race conditions where concurrent operations could pick the same name, causing duplicate config entries and local token revocation issues.