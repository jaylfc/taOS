# taOS native shell

Components of the native taOS desktop shell, built from one tree with CMake
(Qt 6 / QML). The Rust workspace in `native/` (`taos-client`) is separate and
untouched by this directory.

| Component | State |
|---|---|
| `bar/` : `taos-bar`, the status bar | version 0 (this README) |
| `taos-shell-session` | session entry point and supervisor |

## The bar (`taos-bar`)

A layer-shell surface (layer TOP, anchored top, left and right, 40 logical px
high, exclusive zone equal to its height, keyboard interactivity none,
namespace `taos-bar`), one per output. Bars are created and destroyed as
outputs appear and disappear.

Left to right: the **taOS** button (runs the launcher command, default `foot`,
as a detached process: a stand-in until the launcher exists); an empty,
fixed-width slot reserved for the focused window's app name and group chip
(`focusedAppName` / `focusedGroup` in `bar/qml/Bar.qml`; nothing is drawn
while empty, so the layout does not move when it fills); then on the right
network state, volume, battery (only when one exists), date and clock.

Data classes (C++, exposed to QML as singletons in module `Taos.Bar`):

| Singleton | Source | Refresh |
|---|---|---|
| `Clock` | system time, locale aware, `HH:mm` and `ddd d MMM` | on the minute boundary (per second with `--seconds`) |
| `Network` | `/sys/class/net` operstate, `wireless` dir or `/proc/net/wireless`; virtual links ignored; wired wins over Wi-Fi | every 5 s |
| `Volume` | `wpctl get-volume @DEFAULT_AUDIO_SINK@`; hidden when `wpctl` is absent | every 5 s and on click |
| `Battery` | `/sys/class/power_supply/BAT*`; hidden when none | every 30 s |
| `Theme` | `<themes dir>/<id>/theme.json`, see below | once |
| `Launcher` | the command of the taOS button | on click |

### Theme

The bar loads `/usr/share/taos/themes/<id>/theme.json` (directory overridable
with `TAOS_THEME_DIR`), else the built-in copy of the default theme in the
app's resources. Variants are `dark` and `light`. Colours come from an optional
`colors.<variant>` object in `theme.json` (`ground`, `stroke`, `text`,
`textSecondary`, `textTertiary`, `accent`, `warning`, `danger`, CSS order
`#RRGGBBAA`); a package that lacks it (the current `session/themes` one) is
completed key by key from the built-in theme. `motion.reduce: true` in the
theme turns animations off. The bar background is the theme's `ground` at 72%
opacity with a 1 px `stroke` hairline at the bottom.

### Flags

```
taos-bar [--height N] [--variant dark|light] [--launcher-cmd "<cmd>"]
         [--output <name>] [--theme <id>] [--reduce-motion] [--seconds] [--12h]
         [--version] [--help]
```

Environment: `TAOS_THEME_VARIANT` (default `dark`), `TAOS_REDUCE_MOTION=1`,
`TAOS_THEME_DIR`. One line is logged to stderr at start (version, outputs,
theme, height); later lines say when a bar is added to or removed from an
output. SIGTERM and SIGINT exit with status 0. The bar sets
`QT_QPA_PLATFORM=wayland` and `QT_WAYLAND_SHELL_INTEGRATION=layer-shell` itself
when they are unset, and removes the latter from the environment of processes
it launches.

## Building against a Qt prefix

```
native/shell/dev-build.sh <qt-prefix> <layershell-prefix> [build-dir]
RUN_TESTS=1 native/shell/dev-build.sh ...      # also runs ctest
```

`TAOS_EXTRA_CXX_FLAGS` passes compiler flags; on a Debian 13 host with Qt 6.12
(its private headers mention `struct wl_fixes`, a libwayland 1.24 type, while
Debian ships 1.23) use `-include shim.h` where `shim.h` holds `struct wl_fixes;`.
Needs Qt >= 6.8 (Core, Gui, Qml, Quick, Test) and LayerShellQt. Running needs
the Qt libraries and plugins on the library path: see `tests/dev-env.sh.example`.

Tests: `ctest` runs the Qt Test unit tests (clock formatting, network and
battery parsers over a fake sysfs tree, volume parser, theme loader) and
`tests/test-session.sh` (the supervisor, with a stub component).
`tests/e2e-headless.sh` starts a headless sway (never the physical seat), runs
the bar, asserts via `swaymsg` that the usable area starts at y=40 and is 1040
high (scale 1.5: 680 logical), checks hot-plugging an output, SIGTERM and the
absence of QML warnings, and writes `grim` screenshots for dark, light and
scale 1.5. See the script header for its environment.

## Session entry point: `taos-shell-session`

POSIX sh. `start` launches each component in `TAOS_SHELL_COMPONENTS` (default
`taos-bar`) under a supervisor and returns; `stop` ends them using the pidfiles
in `$XDG_RUNTIME_DIR/taos-shell/` (it never signals by name); `status` lists
them (exit 0 all running, 1 a component gave up, 3 something stopped).
`screen-off` and `screen-on` are reserved: they print "not implemented yet" on
stderr and exit 0.

A component that exits non-zero is restarted after 1 s, 2 s, 5 s, then every
30 s (the schedule resets after a run of 60 s or more). After 10 failures
within 5 minutes the supervisor stops, logs that it gave up and leaves a
`<name>.gaveup` marker. A clean exit is not restarted. `/etc/taos/shell.env`
(or `TAOS_SHELL_ENV_FILE`) is sourced first, which is where a packaged Qt
prefix sets `LD_LIBRARY_PATH` and friends. `TAOS_SHELL_BIN_DIR` says where the
component binaries are (default: next to the script, then `PATH`);
`TAOS_SHELL_ARGS_TAOS_BAR="--height 48"` passes arguments to a component.

## Stubbed or not done yet

No focused-window name or group chip (the slot is reserved), no notification
UI, no real launcher, no volume control (click only refreshes), no Wi-Fi
signal strength or SSID, no blur (the compositor does not blur behind layer
surfaces), network state is polled rather than netlink driven, `screen-off` and
`screen-on` are not implemented, and the bar is not hidden for fullscreen apps.
