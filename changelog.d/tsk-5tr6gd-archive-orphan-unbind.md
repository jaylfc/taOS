### Fixed

- Orphan agent deletion (hard-delete and tombstone paths) now revokes the agent's local token binding so the token no longer validates as an admin credential. Previously only the full archive path unbound the token, leaving a failed deploy's token live and allowing a later agent with the same slug to inherit it.