```markdown
### Fixed

- Renamed `test_non_owner_update_returns_403`, `test_non_owner_delete_returns_403`, and `test_non_owner_archive_returns_403` to use `404` instead of `403` for non-owner mutation attempts, per project ownership design. This closes the existence oracle where "exists but forbidden" is indistinguishable from "does not exist" (from tsk-ob2mpd).

- Added `test_non_owner_oracle_closed` to verify the oracle is actually closed: both missing and forbidden project IDs return identical 404 response bodies.
```