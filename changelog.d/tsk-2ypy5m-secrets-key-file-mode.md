### Fixed

- **Secrets key file is now forced to 0600 on every load (tsk-2ypy5m)** -- an existing `.secrets_key` that was group- or world-readable (for example 0644 from an older install, a copy, or a restore) is tightened to 0600 when loaded, with one warning, and a key owned by another user is refused instead of silently read. The GitHub identities store reuses the same key and is covered too.
