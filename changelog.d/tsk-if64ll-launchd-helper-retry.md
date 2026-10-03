### Fixed
- Launchd reload helper now retries `launchctl bootstrap` up to 5 times (2s delay) and only deletes itself after verified success (`launchctl print` succeeds). Failed boots leave the helper plist in place for RunAtLoad retry at next login.
- Branch-switch (`/api/settings/update-channel`) now surfaces launchd migration warnings in the success response, matching `/api/settings/update` behavior.
- Tests for launchd migration now patch both `PLIST_PATH` and `HELPER_PLIST_PATH` to tmp_path, preventing real ~/Library writes.