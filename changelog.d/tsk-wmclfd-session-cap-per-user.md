### Fixed

- The auth session store now caps live sessions per user at 50: minting a new session past the cap evicts that user's oldest sessions in the same locked write, so a client that logs in on every invocation can no longer grow `.auth_sessions` without bound (37,521 live sessions for one account were measured on a Pi). Expired-entry reaping on write was already in place; session validation, User-Agent binding and revocation are unchanged.
