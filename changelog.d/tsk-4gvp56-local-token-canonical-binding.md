### Security

- Bind per-agent local tokens to the registry canonical id and reject tokens when the agent is replaced under the same name.

### Fixed

- The canonical-id binding caused a restore regression: `_restore_archived` minted the bound local token, started the container and restarted the bridge before the config entry was appended, so the restored bridge's bootstrap request answered 401; the config entry is now appended before `start_container`, and a validated local token with no usable binding entry fails closed to 401 instead of falling through to unbound primary-user admin (the host's own unbound local token still passes).
