### Added

- Password reset foundation: a server-side `PasswordResetStore` (`tinyagentos/password_reset_store.py`) that mints cryptographically-random reset tokens, persists only their SHA-256 hash (never the plaintext), enforces a 30-minute TTL, consumes tokens with one atomic `UPDATE ... WHERE used=0` to defeat double-spend races, invalidates a user's prior outstanding tokens when a new one is minted, and is wired on `app.state.password_reset` via the real app factory.
