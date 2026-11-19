### Added

- The kiosk session can now show a system on-screen keyboard (squeekboard) on devices that opt in with `install-session.sh --keyboard` or `taos-session keyboard on`. It is off by default.
- Theme packages can restyle the keyboard: `taos-session theme <id> <dark|light>` applies one, and falls back to the default dark theme if the package is incomplete.
- The session installer now works on Debian as well as Alpine and postmarketOS.
