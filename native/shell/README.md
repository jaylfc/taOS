# taOS native shell

Components of the native taOS desktop shell, built from one tree with CMake
(Qt 6 / QML). The Rust workspace in `native/` (`taos-client`) is separate and
untouched by this directory.

| Component | State |
|---|---|
| `bar/` : `taos-bar`, the status bar | version 0 (this README) |
| `greeter/` : `taos-greeter`, the login screen (a greetd greeter) | version 0 (section below) |
| `weather/` : `taos-weather-cache` and its systemd units | opt-in weather for the login screen |
| `common/` | theme loader shared by the bar and the greeter |
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
| `Audio` | PipeWire through libpulse (`pipewire-pulse`), subscribed to server events | on events only, never on a timer |
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

## The login screen (`taos-greeter`)

A Qt 6 / QML application with a C++ backend that talks to greetd. It is an
ordinary fullscreen xdg-toplevel (not a layer-shell surface): greetd starts it
inside the `cage` kiosk compositor as the greeter user.

**Composition.** The wallpaper is drawn by the greeter (the same pick as the
session: default id of `index.json`, the dark or light variant, the file whose
orientation and aspect fit the output best), so the glass can blur it. Every
shipped wallpaper has the "taOS" wordmark rendered into the image at the exact
centre; the layout composes around it and never covers it (`LayoutMath` in
`greeter/src/layoutplan.cpp` holds the measured wordmark box per aspect ratio;
`tests/wordmark_check.py` compares screenshots with the wallpaper to prove the
wordmark is untouched). Above it: a very large light clock (24 hour by default,
`--12h` for 12 hour, tabular figures), the date, and, when a cache file exists,
a one line weather summary. Below it: a macOS style user selector (96 px round
avatars with the display name) and, once a user is chosen (immediately when
there is only one), a single pill shaped password field with an inline round
arrow, a spinner while greetd works, a Caps Lock mark, and one message line.
Bottom: hostname (left), Sleep, Restart and Shut Down (right; the first click
turns the button into "Restart?" for 4 seconds, the second one runs it).
Under the selected name a caption says "Account on this computer": it is the
computer's own account, not an email address or an online account. There is no
username field and no session chooser.

**Users** are found at run time: AccountsService over D-Bus (`ListCachedUsers`,
skipping system and locked accounts) when it answers with users, else
`/etc/passwd` (uid 1000 to 59999 with a real login shell; name from the first
GECOS field). Avatar: AccountsService `IconFile`, `/var/lib/AccountsService/icons/<user>`
or `~/.face` when readable, else a monogram on a gradient in the accent colour
family derived from a hash of the user name. No user name is hard coded.

**Keys.** Left and Right move between users, Enter or a click selects, Escape
(or a click on the background) goes back to the user row when there are several
users, Tab reaches the power buttons, Enter submits the field. Focus rings show
only once a key was pressed. Caps Lock is learned from the first key press.

### Flags and environment

```
taos-greeter [--demo] [--demo-users N] [--demo-otp] [--session-cmd "<cmd>"]
             [--variant dark|light] [--theme <id>] [--reduce-motion] [--12h]
             [--size WxH] [--version] [--help]
```

`--session-cmd` defaults to `taos-desktop-session`. `--demo` runs against a
built-in fake greetd (it speaks the real protocol on a private socket): the
password is `demo`, wrong ones fail after 600 ms, `--demo-users N` (1 to 6)
adds invented placeholder users, `--demo-otp` adds a second visible prompt
(code `123456`); the power buttons only echo in demo mode and the process
prints `demo: session start: <cmd> (user <name>)` and exits 0 after a login.
`--size WxH` opens a window of that size instead of going fullscreen.

Environment: `GREETD_SOCK` (set by greetd), `TAOS_THEME_VARIANT`,
`TAOS_REDUCE_MOTION=1`, `TAOS_THEME_DIR`, `TAOS_WALLPAPER_DIR`,
`TAOS_WEATHER_FILE` (default `/var/cache/taos/weather.json`),
`TAOS_GREETER_POWER_CMD` (replaces `systemctl`; run with `suspend`, `reboot` or
`poweroff`), `TAOS_GREETER_PASSWD` and `TAOS_GREETER_NO_DBUS=1` (user discovery
in tests), `TAOS_GREETER_DEBUG=1` (phase changes on stderr). Without
`GREETD_SOCK` (and without `--demo`) the screen shows a plain message and the
process exits 1 after 5 seconds, so a misconfiguration is visible and never a
black screen. With no users it shows "No one to sign in" and stays up so the
power buttons keep working. Motion (appear, spring selection, shake) is removed
by `--reduce-motion`, `TAOS_REDUCE_MOTION=1` or `motion.reduce` in the theme.
The cursor hides after 10 s of inactivity.

### The greetd contract

Frames are a 4 byte native-endian length and a JSON payload (greetd-ipc(7)).
`GreetdClient` runs: `create_session` then any number of `auth_message` rounds
(`visible` and `secret` are questions and surface as `promptRequested`; `info`
and `error` are shown as a message and acknowledged with a null response)
answered by `post_auth_message_response`, then `success`, then `start_session`
with the session command, then `success`, then the greeter exits and greetd
starts the session. After any `error` it sends `cancel_session` before
`authFailed` is reported, and a login can start again at once. Frames over
1 MiB, zero length frames, malformed JSON, unknown reply types, unsolicited
replies, a closed socket and a missing answer to `cancel_session` or
`start_session` within 20 s are fatal (the screen says so and exits 1). Waiting
for the user, and for PAM (fingerprint, touch key, lockout delay), is never
timed out.

### Weather (opt in, no network in the greeter)

The greeter never makes a network request. It reads
`/var/cache/taos/weather.json` and shows weather only when the file is under
3 hours old and well formed; otherwise nothing is drawn (no placeholder, no
error). `weather/taos-weather-cache` (POSIX sh and curl) writes that file
atomically from Open-Meteo current conditions. It does nothing, and exits 0,
unless `/etc/taos/weather.conf` exists. No location is ever guessed or looked up
from the IP address:

```
# /etc/taos/weather.conf
LATITUDE=0.0
LONGITUDE=0.0
PLACE=Example Town
UNITS=metric        # or imperial
```

Output fields: `place`, `temperature`, `units`, `code` (WMO), `is_day`,
`condition`, `icon`, `updated` (UTC, ISO 8601). `weather/wmo-codes.tsv` is the
single table both the script and the greeter are tested against.
`taos-weather-cache.service` and `.timer` (every 30 minutes) are shipped as
files and not installed by anything here. The service runs as the fixed
unprivileged user `taos-weather` (`taos-weather.sysusers`) with
`CacheDirectory=taos`, not as a dynamic user: a dynamic user's cache directory
lives under the root-only `/var/cache/private`, which the greeter's own account
could never read. Open-Meteo data is licensed CC BY 4.0 (see THIRD-PARTY.md).

### Putting it live

`greeter/taos-greeter-session` is the command greetd runs (cage plus the
greeter, with the Qt runtime pinned to `/opt/taos/qt`); `greeter/stage-qt-runtime.sh`
copies the part of a Qt installation the greeter needs (about 83 MB without
WebEngine, Quick Controls or QML tooling) into one directory the greeter user
can read. A machine whose GL stack does not work can set `TAOS_GREETER_SOFTWARE=1`
(Mesa llvmpipe; the Qt Quick software backend cannot draw the effects).

### What is stubbed or unverified

Tested only against a scripted greetd and the built-in fake one: real greetd,
real PAM (including fingerprint and one time codes), AccountsService on a seat,
the power actions and a physical keyboard's Caps Lock state are not exercised.
Keyboard layout switching is not shown (no cheap way to read the active layout).
The weather icons and clock use the Inter font from the system; if it is
missing Qt falls back to another sans.

### Tests

`ctest` runs the greeter unit tests (greetd client against a scripted
local-socket server, passwd parser, monograms, weather parsing and freshness,
WMO mapping, wordmark layout at four sizes plus seven more, wallpaper pick,
power confirm, and a contrast test that samples every shipped wallpaper under
the real glass and scrim numbers) and `weather/tests/test-weather-cache.sh`
(stubbed curl). `tests/e2e-greeter.sh` drives `taos-greeter --demo` under a
headless sway at 3840x1200, 3840x2160, 1920x1080 and 1080x2400 with `wtype`
and `grim`, and also a non-demo run against `tests/fake-greetd.py`.

## Building against a Qt prefix

```
native/shell/dev-build.sh <qt-prefix> <layershell-prefix> [build-dir]
RUN_TESTS=1 native/shell/dev-build.sh ...      # also runs ctest
```

`TAOS_EXTRA_CXX_FLAGS` passes compiler flags; on a Debian 13 host with Qt 6.12
(its private headers mention `struct wl_fixes`, a libwayland 1.24 type, while
Debian ships 1.23) use `-include shim.h` where `shim.h` holds `struct wl_fixes;`.
Needs Qt >= 6.8 (Core, Gui, Network, DBus, Qml, Quick, Test) and LayerShellQt. Running needs
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

## Sound menu and volume keys

The volume icon opens a panel under it (same window kind, look and keys as the
network menu; one menu at a time). Top to bottom: output volume with a mute
switch; the outputs that are actually available, named for people ("USB
headset", "Headphones (Sony WH-1000XM4)", "Monitor speakers (HDMI 2)",
"Speakers", "Line out (rear jack)") with a tick on the current one; a mode
choice where it matters ("High quality music" or "Headset with microphone"
for Bluetooth, analog or HDMI on one card); then the microphone with a live
level meter, its volume and mute, and the input list. The icon shows none
(no output), muted, or one to three waves.

Backend: libpulse, which PipeWire serves through `pipewire-pulse` (Debian's
`pipewire-audio` pulls it in with WirePlumber and the Bluetooth plugin). It
runs on its own libpulse thread and changes arrive from `pa_context_subscribe`
events (sinks, sources, cards, streams, server): there is no polling and
`wpctl` is not used. Unplugged ports and unavailable card profiles (the empty
HDMI outputs of a graphics card) are hidden. Choosing an output sets the
default and moves the streams that are already playing. The level meter is a
peak-detect record stream that exists only while the menu is open. If the
sound server is not running the menu says "Sound is not available on this
computer" and the bar retries every 3 s until it appears; that retry timer
runs only while disconnected.

Keys: `taos-bar volume up|down|mute|mic-mute` (also `taos-bar mic-mute`) talk
to the running bar over `$XDG_RUNTIME_DIR/taos-bar.sock` (`TAOS_BAR_SOCKET`
overrides) and the bar flashes the level at the bottom centre of the main
screen. Steps are 5 points and never go above 100%. Bind the keys with
`sway/50-taos-audio-keys.conf`. `TAOS_BAR_PULSE_SERVER` points the bar at
another sound server (tests, demos).

Tests: `test_audiostate` (naming, filtering, profile choices, from written-out
data), `test_audio` (the controller against a recording fake), `test_baripc`,
and `test_pulsebackend`, which starts a private PipeWire, WirePlumber and
pipewire-pulse in a temporary runtime directory and is skipped when those
tools are not on PATH. `tests/qmlshot.cpp` renders the bar and both menus with
fake data to a PNG (offscreen, software renderer; no shadows there).

## Network menu (Wi-Fi, Ethernet, Bluetooth)

Clicking the network icon opens a frosted panel under it (a layer-shell surface
on the OVERLAY layer covering the output, so a click outside, or Escape, closes
it; Tab and the arrow keys move between rows, Enter or Space activates, Delete
forgets or disconnects). It talks D-Bus from C++ (QtDBus), never through
`nmcli` or `bluetoothctl`, and everything is signal driven: nothing polls while
the menu is closed, and the scan, discovery and the Bluetooth pairing agent
run only while it is open.

| Section | Backend | Shows |
|---|---|---|
| Wi-Fi | NetworkManager (`org.freedesktop.NetworkManager`, system bus) | on/off switch, current network first with a tick, others by signal, padlock on secured ones, saved ones can be forgotten, "Other network..." for hidden SSIDs, password asked in a pill field and sent only over D-Bus |
| Ethernet | NetworkManager, or `/sys/class/net` without it | "Connected, 1 Gbit/s", "Cable unplugged"; hidden when the machine has no wired port |
| Bluetooth | BlueZ (`org.bluez`, system bus) | on/off switch, paired devices with connected state and battery, "Nearby" while discovering, pair; passkey confirm, PIN and permission requests are answered in the menu (a BlueZ `Agent1` registered while the menu is open) |

The menu needs NetworkManager and BlueZ running. Without them it says so in
plain words and does not fail: "Wi-Fi is not available on this computer",
"This computer has no Wi-Fi adapter", "Bluetooth is not available on this
computer", "This computer has no Bluetooth adapter". Without NetworkManager the
bar icon falls back to the `/sys` reading (and polls it every 5 s, as before),
and Ethernet still shows from `/sys`. Passwords, PSKs and link keys are never
logged; BlueZ keeps the link keys itself.

Permissions (Debian 13, NetworkManager 1.52): scanning and the Wi-Fi switch are
allowed for the active local user; saving a connection needs
`settings.modify.system`, which Debian's polkit rule grants to local active
sessions of members of `sudo` or `netdev`. Anyone else gets "This account is
not allowed to join Wi-Fi networks on this computer". BlueZ has no polkit
policy. The compositor does not blur behind layer surfaces, so the panel tint is
denser than the greeter's (`MenuPanel.qml`, `tintAlpha`).

Tests: `test_nmclient` and `test_bluezclient` run the real clients against fake
NetworkManager and BlueZ services on a private `dbus-daemon --session` started
by the test. `TAOS_BAR_DBUS_ADDRESS` points the bar at such a bus (demos).

## Stubbed or not done yet

No focused-window name or group chip (the slot is reserved), no notification
UI, no real launcher, no blur (the compositor does not blur behind layer
surfaces), `screen-off` and
`screen-on` are not implemented, and the bar is not hidden for fullscreen apps.
