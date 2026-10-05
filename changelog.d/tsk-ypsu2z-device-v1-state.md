### Added

- GET `/api/device/v1/state` (device bearer, scope `agents:read`): returns the owner-filtered agent list for the paired device, plus `server.version`, `time`, and `demo` flag. Server-side string caps (name 48, status 120, `last_recap` 180, question 280, option 40) with trailing ellipsis. `avatar.hash` is the first 16 hex chars of the avatar image SHA-256, or null when no image is installed.

### Security

- `/api/device/v1/state` is registered in the device-bearer allowlist (`_DEVICE_BEARER_PATHS`) and is CSRF-exempt like the other device-bearer routes. Only a `taosdev_...` bearer with the `agents:read` scope may reach it; missing scope returns 403 with `device_scope_missing`.
