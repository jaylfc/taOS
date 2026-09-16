### Fixed
- Bind CSRF double-submit tokens to the current `taos_session` with an HMAC, rejecting tokens minted for another session.
