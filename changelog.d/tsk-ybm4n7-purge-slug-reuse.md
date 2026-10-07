### Fixed

- Purging an archived agent no longer revokes a live agent's local token when that agent redeployed onto the freed slug; the archived agent's local-token binding is now revoked at archive time instead, and purge skips the unbind when a live agent holds the slug.
