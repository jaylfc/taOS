### Added

- Desktop distro step 8: `taos-desktop-session` package with the graphical login screen (greetd, gtkgreet in a cage kiosk on VT 7, interim until the native greeter) and the taOS sway session started through `/usr/bin/taos-desktop-session`.
- The greeter and the session open only the display GPU (`/dev/dri/taos-display` from taos-gpu), so compute-only NVIDIA cards never drive the screen.
- `/usr/lib/taos/desktop-session/autostart` is the single entry point that starts the graphical shell (`taos-shell-session start`, if installed); `/etc/taos/sway.d/` and `~/.config/taos/sway.d/` take drop-ins; the keyboard layout is generated from `/etc/default/keyboard`.
- `taos-desktop enable|disable|status`: installing the package does not start greetd, switch the target or activate the taOS login config; `enable` turns the login on (greetd, graphical target, not started), `disable` reverses it.
