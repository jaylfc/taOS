### Fixed

- Desktop Wi-Fi panel: the empty in-flight status region no longer keeps its 12px bottom margin, so it does not leave a gap when there is no status to announce.
- Desktop layout store: the unsaved preference marker is persisted atomically with the cached value, so a quota failure on the marker write rolls the cache back and a reload cannot sync an older server value over the local choice.
- Desktop channel party: an archived DM keeps its channel title while the archived agent roster loads instead of flipping to the member slug.

### Added

- Desktop Wi-Fi panel tests: cover a pending-refresh Disconnect, verify Tab reaches the Join button, and assert the row's action after disconnect.
- Desktop first-chat test: assert the full save sequence (original key, then rotated key) after a 409 refusal.
- Desktop ModelStep test: enable fake timers before starting the download.
