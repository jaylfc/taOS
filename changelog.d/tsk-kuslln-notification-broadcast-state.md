### Fixed

- Fixed per-user broadcast read/archived state so `list()` and `unread_count()` correctly exclude broadcasts archived by a specific user, and the `unread_count` query no longer references an undefined table alias.
