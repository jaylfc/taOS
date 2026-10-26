### Fixed
- `_nm_add_and_activate` now returns `dbus_call_failed` when the system bus connection fails (e.g. missing D-Bus socket in containers) instead of raising an unhandled OSError that caused a 500 error.
