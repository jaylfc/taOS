### Fixed
- deregister now uses `_require_admin` (session cookie only) instead of `require_admin` (also accepts host local token), aligning it with revoke/block/unblock
