### Fixed
- launchd migration now writes the original plist bytes to `.bak` BEFORE overwriting the live plist, so the bare-uvicorn original is recoverable
- `_write_reload_helper` now quotes `controller_plist` and `HELPER_PLIST_PATH` with `shlex.quote` so paths containing spaces or shell metacharacters are not misparsed
