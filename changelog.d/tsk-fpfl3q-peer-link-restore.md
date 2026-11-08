### Fixed
- establish_peer_link honours allow_revoked
- the stray revoked_at UPDATE was removed from friend-accept
- the owner's friend-accept restores a revoked link while a concurrent revoke of a live link is still refused