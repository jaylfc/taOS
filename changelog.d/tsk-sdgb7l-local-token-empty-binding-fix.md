### Fixed

- Empty or whitespace-only bound agent name in local token bindings now fails closed (401) instead of falling through as primary-user admin. Restore now rolls back in-memory config mutations if `save_config_locked` raises.
