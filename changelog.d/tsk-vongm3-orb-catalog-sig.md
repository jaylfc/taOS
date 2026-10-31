### Added

- Orb catalog signing and serial high-water state (tinyagentos/orb/catalog_sig.py):
  - Ed25519 signature verification with pinned keys (CATALOG_KEYS)
  - Catalog signature validation: key_id format, base64 sig decode, 64-byte check, single-key lookup (no try-all fallback)
  - Serial high-water mark enforcement with atomic state file (orb/catalog_state.json)
  - State corruption detection (never falls back to serial 0)
  - Expires field ISO 8601 parsing (accepted but not enforced)
  - Admin reset_state() for state file removal
  - Lazy cryptography imports (mirrors agent_registry_store.py pattern)
  - 6 exception classes, all OrbError subclasses