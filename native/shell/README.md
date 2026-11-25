# taOS native shell

Components of the native taOS desktop shell, built from one tree with CMake
(Qt 6 / QML). The Rust workspace in `native/` (`taos-client`) is separate and
untouched by this directory.

| Component | State |
|---|---|
| `bar/` : `taos-bar`, the status bar | version 0 (this README) |
| `greeter/` : `taos-greeter`, the login screen (a greetd greeter) | version 0 (section below) |
| `overview/` : `taos-overview`, the Alt+Tab window switcher and the workspace overview | version 0 (section below) |
| `weather/` : `taos-weather-cache` and its systemd units | opt-in weather for the login screen |
| `notifyd/` : `taos-notifyd`, the freedesktop notification server | version 0 (section below) |
| `common/` | theme loader and glass style shared by the bar, the greeter and the overview |
| `protocols/` | vendored Wayland protocol XML (ext-foreign-toplevel-list, ext-image-capture-source, ext-image-copy-capture) |
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

The person's choice is `~/.config/taos/appearance.json` (`{"theme","mode"}`, mode
dark, light or auto), changed live from the Appearance section of the Display
menu or with `taos-theme`; the bar, menus, overview, greeter and native apps
follow without restarting. Themes: the shipped graphite, graphite-ice and
graphite-warm, plus any in `~/.local/share/taos/themes` and
`/usr/share/taos/themes`. Format, validation and the greeter's rules:
`common/THEMES.md`. `--theme` and `--variant` (or `TAOS_THEME`,
`TAOS_THEME_VARIANT`) override the file for that process. Colour changes
cross-fade over 150 ms, not under `--reduce-motion`.

### Flags

```
taos-bar [--height N] [--variant dark|light] [--launcher-cmd "<cmd>"]
         [--output <name>] [--theme <id>] [--reduce-motion] [--seconds] [--12h]
         [--version] [--help]
```

Environment: `TAOS_THEME_VARIANT` (dark, light or auto; default: appearance.json), `TAOS_REDUCE_MOTION=1`,
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

## The window switcher and overview (`taos-overview`)

A daemon that stays hidden until asked. It owns one layer-shell surface (layer OVERLAY, all four
edges, exclusive zone -1, namespace `taos-overview`, exclusive keyboard while shown), on the output
of the focused workspace; the same binary is the client (`taos-overview switcher` and friends).

```
taos-overview daemon [--variant dark|light] [--theme id] [--reduce-motion] [--no-capture]
taos-overview switcher [--reverse] [--sticky]   open the switcher, or move its selection
taos-overview overview                          open the overview, or close it
taos-overview hide | commit | next | prev | left | right | up | down | status
```

The commands travel over a Unix socket, `$XDG_RUNTIME_DIR/taos-shell/overview.sock` (mode 600 in a
mode 700 directory). The client is plain POSIX code that runs before any Qt object exists, so a key
binding that execs it costs a socket write. `taos-shell-session` starts and supervises the daemon
next to `taos-bar` (default `TAOS_SHELL_COMPONENTS="taos-bar taos-overview taos-dock taos-frame"`).

**Switcher (Alt+Tab).** A glass panel with a row of live thumbnails in most-recently-used order, the
app icon on each and the title under it; the previous window is preselected. The first Alt+Tab opens
it. While the overlay owns the keyboard sway still matches its key bindings first, so every further
Tab (and Shift+Tab) re-runs `taos-overview switcher [--reverse]`, which moves the selection while it
is up; arrows, Q (close the selected window, `kill` over sway IPC), Escape (cancel) and the mouse reach
the overlay itself. Releasing Alt focuses the selection (`[con_id=N] focus`, which also changes
workspace). If Alt was already released when the overlay got the keyboard, it commits at once. `--sticky`
keeps the switcher up until Return or Escape (for scripts and tests). One window: shown alone. No
window: nothing is shown.

**Overview (Super tap).** Fades and blurs the desktop (swayfx `layer_effects`), packs the live
thumbnails of the current workspace in a Mission Control style layout (aspect ratios kept, one shared
scale, no overlap, reading order kept), with a strip of workspace miniatures on top (the windows'
thumbnails composed at their positions; click one to go there, the last tile makes a new workspace).
Hover lifts a thumbnail and shows its title; click focuses; Escape, or a second Super tap, closes;
typing filters by title, app or workspace across ALL workspaces; arrows and Tab move the selection and
Return focuses it.

**Window model.** Sway IPC over `$SWAYSOCK` directly: one `get_tree` at start, then the `window` and
`workspace` events patch the model (a title change patches one string; only structural events refetch,
after a 20 ms debounce). Most recently used order is kept from the focus events (one seat: sway has one
default seat here). Each window carries con_id, app_id (or X11 class), title, workspace, output, focused,
urgent, floating, geometry and `foreign_toplevel_identifier` (the exact field name in sway 1.12's
`get_tree`). Icons come from the app's `.desktop` file (`Icon=`, indexed on a background thread) through
the icon theme; without one the tile is a monogram in the greeter's avatar style.

**Live thumbnails.** A second Wayland connection on its own thread binds `ext_foreign_toplevel_list_v1`
(handles matched to sway windows by identifier), `ext_foreign_toplevel_image_capture_source_manager_v1`
and `ext_image_copy_capture_manager_v1`. Capture sessions exist only while the overlay is visible and are
destroyed when it hides; frames go through `wl_shm` buffers and are scaled down on that thread, so only
thumbnail-size images reach the GUI, which paints them as textures. The newest image of each window is
cached and shown at once on the next open. The rate is 15 fps for up to 4 thumbnails, 12 up to 8, then 8
and 5. Windows on other workspaces and covered windows are captured too (verified under sway 1.12).
dmabuf capture is a later optimisation: the session offers shm formats only to this client for now.

**Motion and accessibility.** Open and close are a 200 ms fade with a slight scale; selection moves
animate; everything is off under reduced motion (theme `motion.reduce`, `--reduce-motion`,
`TAOS_REDUCE_MOTION=1`). The overlay exposes the windows as a list ("Terminal: ~/src, workspace 2"),
announces selection changes, and everything works from the keyboard.

**Fail-safes.** The overlay owns the keyboard while up, so: it closes itself after 30 s without a key,
click or command (`TAOS_OVERVIEW_IDLE_MS` overrides); a watchdog thread exits the process if the GUI
thread stops beating for 6 s while the overlay is up (the compositor then drops the surface and the
supervisor restarts the daemon); `kill -9` leaves nothing behind, as the keyboard grab ends with the
surface (tested); a non-sticky switcher that never receives the keyboard hides after 0.8 s.

**Idle cost.** Hidden, the daemon has no timer and no capture session; the capture thread and the
watchdog sleep in `poll` and a condition variable. Sway events patch the model only.

**Installing the key bindings.** `overview/sway/40-overview.conf` goes to
`/etc/taos/sway.d/40-overview.conf` (the installed copy is `share/taos/sway.d/`):

```
bindsym Mod1+Tab exec taos-overview switcher
bindsym Mod1+Shift+Tab exec taos-overview switcher --reverse
bindsym --release Super_L exec taos-overview overview
bindsym Mod4+Tab exec taos-overview overview
layer_effects "taos-overview" blur enable; blur_ignore_transparent enable
```

`bindsym --release Super_L` was checked headless: it does not fire after Super+Return or Super+x (sway
runs a release binding only when no other key went down meanwhile). Not covered: a Super+mouse drag
(`floating_modifier`) followed by a Super release may open the overview; Super+Tab is the plain
alternative if that bothers. `tests/e2e-overview.sh` drives all of it under a headless sway (see its
header); the unit tests (`test_swaytree`, `test_windowlist`, `test_packing`, `test_ipc`) run in `ctest`.

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

## Notifications (`taos-notifyd`)

A UI-less Qt service (QtCore, QtDBus, QtNetwork) that owns `org.freedesktop.Notifications` on the person's
session bus (Notify, CloseNotification, GetCapabilities, GetServerInformation, NotificationClosed,
ActionInvoked; spec 1.2) and forwards every notification it accepts to the person's own controller with
`POST /api/notifications/os`, so anything that uses `notify-send` or the desktop interface lands in the
Notifications app. **Opt-in**: it is not in the default components, because it has no popup yet and would take the
notification bus name from whatever shows them today. Enable it by adding `taos-notifyd` to `TAOS_SHELL_COMPONENTS`
(in `/etc/taos/shell.env`), or install with `install-desktop.sh --with-notifyd`. Later installs and updates keep it when it is already in the
line (a person's choice is never undone); `--without-notifyd` takes it out. Once enabled, `taos-shell-session`
supervises it; the bus name is the single-instance lock (it never replaces another server: if the name is taken it
writes `idle` and the reason to `$XDG_STATE_HOME/taos/notifyd/status` and exits 0).

* **Never lost.** The notification is written to disk (file fsynced, renamed, directory fsynced) before
  `Notify` returns, in `$XDG_STATE_HOME/taos/notifyd/` (mode 0700, files 0600), and deleted only when the
  controller answers 200 with the route's JSON. One record is in flight at a time, oldest first, retried
  with backoff (1 s doubling to 60 s; `Retry-After` honoured on 429; 401, 403, 404, 5xx and a dead controller
  all keep the record). A 400, 413 or 422 moves the record to `rejected/` and raises a visible warning record. If the warning cannot be numbered (the id
  counter cannot be saved), the refusal is logged and `status` reports `rejected_unwarned` and a `warning`; the warning
  record is raised as soon as the counter works again.
  The client id is minted once at accept, so a retry after a lost answer stores one row. It is
  `n<install id>.<boot id>.<counter>`: the install id is random, made once per state directory, the boot id is
  random per run, so a wiped directory or a lost counter never reuses an id. A counter that cannot be saved fails
  the `Notify` call with a D-Bus error.
* **Bounded.** More than 500 pending non-critical records: the oldest 50 are dropped, but only after a
  critical warning record ("Some notifications were dropped") is on disk and full copies are appended to
  `dropped.jsonl`. Critical (urgency 2) records are never dropped; at 5000 pending records `Notify` fails with
  a D-Bus error, so the app sees it.
* **Credentials.** The shared desktop session file `$XDG_RUNTIME_DIR/taos/session` (owner only, 0600),
  read afresh for each attempt: its controller, exact User-Agent, session token and CSRF token. With no
  sign-in yet, notifications are buffered and sent once the person signs in.
* **Filters.** `TAOS_NOTIFYD_DROP_ENTRIES` (comma separated desktop entries or app names, empty by default):
  `Notify` from these returns normally and is not buffered (the phone sets the kiosk browser here: its web
  notifications already exist as controller rows, spec test N9). Transient notifications are not kept (there is
  no popup to show them), but each one is counted in the `status` file (counts, never content); critical ones
  are kept like any other. Markup is removed from every text field, and the Notifications app shows app
  supplied text as plain text (`TaosText` defaults to `Text.PlainText`). A replace only applies to a
  notification the same bus connection raised in this run.
* **Lock rule** (`src/lockgate.*`, spec 7.8, test L8): `GET /auth/lock-state` with the console header; only
  the exact `{"locked":false,"session":n}` is unlocked, everything else is locked. `popupForm()` gives what a
  popup may show at the person's lock level. There is no popup yet, so nothing draws it.
* **Not built yet:** a popup, the action return path (ActionInvoked, dismiss in the Notifications app closing
  the notification in the sender; it needs a controller route for actions and a per-user event the daemon
  subscribes to), and archiving the row on `CloseNotification`.

Logs carry counts and app keys, never titles or bodies.

## Session entry point: `taos-shell-session`

POSIX sh. `start` launches each component in `TAOS_SHELL_COMPONENTS` (default
`taos-bar taos-overview taos-dock taos-frame`) under a supervisor and returns; `stop` ends them using the pidfiles
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
another sound server (tests, demos). `TAOS_BAR_OPEN_MENU=network|audio` opens
that menu two seconds after start (screenshots).

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

## Display (scale, refresh, native resolution)

`taos-display` (`display/`) keeps every output at the monitor's own shape and
speed and scales the interface instead. The bar's monitor icon opens the menu.

**Native mode.** The resolution is the monitor's preferred one, at the highest
refresh offered at that resolution. A bigger non-preferred resolution (a
3840x1200 ultrawide also lists 3840x2160) is never chosen. sway 1.12 does not
say which mode is preferred and does not report the panel's physical size (its
`get_outputs` has neither field; checked in the pinned swayfx 0.6.1 source), so
both come from sysfs: the first line of `/sys/class/drm/card*-<connector>/modes`
(the kernel lists the EDID's preferred mode first) and the EDID in `.../edid`.
A `preferred` flag in the JSON wins when a compositor provides one. With no
information at all the running resolution is kept (highest refresh there).

**Scale.** Presets 100, 125, 150, 175, 200 percent, applied live with
`output <name> scale <v>` and saved at once; spoken as "Scale 125 percent".
Fractional scales are crisp for Qt and GTK4 apps; X11 (XWayland) apps are scaled
up, and the menu says so only while such a window exists. The recommended scale
aims for about 125 logical dpi (the 110 to 140 band), rounds to the nearest
preset and never goes below 100%; an unknown or implausible physical size gives
100%. The 3840x1200 ultrawide (about 43 inch) comes out at 100%.

**Keep this setting?** A mode or refresh change is applied, read back, and then
the menu asks for confirmation. It goes back by itself after 10 seconds, on
Escape, or on a click outside. The check does not trust swaymsg's exit code: the
output is read back, twice, and a mode that did not take or that blanked the
output is undone at once. A mode that failed or was turned down is not offered
again this session (so the output events a change causes cannot loop). Scale
changes need no confirmation.

**Where it lives.** `~/.config/taos/sway.d/20-display.conf`, generated, written
atomically (temp file, fsync, rename) and only when the content changes, keyed
by `make model serial` (what `output "<id>"` matches) and not by connector, so
the settings follow a monitor to another port. Entries of monitors that are not
plugged in are kept. `/etc` is never touched.

**Session.** `exec_always taos-display apply` (sway/45-taos-display.conf) applies
the saved settings and the recommended scale for a monitor seen for the first
time. A first-run mode change is left to the bar, which subscribes to sway's
`output` events, applies on hotplug and asks the Keep question; `taos-display
apply --now` applies it at once with the same verify and undo, for a session
without a bar. Also: `taos-display status`, `taos-display scale <output>
<percent>`, `taos-display watch`.

Tests: `test_displaymodel` (mode choice from fixture `get_outputs` JSON: the
ultrawide, a laptop panel, two outputs, no preferred information; scale from
physical size; sysfs and EDID), `test_displayconfig` (writer: keys, quoting,
atomic, idempotent), `test_displaycontroller` (a scripted compositor: revert on
refusal, on a mode that is ignored, on a blanked output, the 10 second trial on
a fake clock, scale commands, hotplug echo, the real `swaymsg` runner and the
output watcher). Menu renders with fake data: `qmlshot display out.png
[--fixture two.json] [--confirm] [--xwayland] [--set-scale 1.1]`.

## Stubbed or not done yet

No focused-window name or group chip (the slot is reserved), no notification
UI, no real launcher, no blur (the compositor does not blur behind layer
surfaces), `screen-off` and
`screen-on` are not implemented, and the bar is not hidden for fullscreen apps.

Display, not verified on a real compositor: the real `swaymsg` against a live
sway (only a scripted one is tested), whether the menu survives the output
reconfiguration of a mode change, XWayland detection on a real tree, the
keyboard path in the real layer surface, and that 120 Hz over the owner's cable
really lights up (the Keep question is the safety net). No arrangement editor;
monitors with the same make, model and serial share one setting.

## Dock (`taos-dock`)

The application dock: a layer-shell strip with pinned apps, running apps and minimised windows. Settings
live in `~/.config/taos/dock.json` (`position` bottom|left|right, `attached`, `autohide`, `size`
small|medium|large, `magnify`, `hideInTiling` (default true), `pinned`, `outputs` primary|all) and a running dock follows the file live.
`taos-dock get|set <key> <value>|pin <id>|unpin <id>|list` edits it. Apps come from `.desktop` files; a window
belongs to an app by its app_id (file id or StartupWMClass). Minimised state lives in the compositor (the sway
mark `taos_min_<conid>`); the dock keeps none, so a restarted dock rebuilds from the window list. The window
engine behind it is `SwayEngine` (a fallback to be replaced by `taos_window_core`) or, with
`TAOS_DOCK_ENGINE=cli`, the `taos-window` tool. Include `sway/47-taos-dock.conf`. The six native apps (Activity, Notifications, Decisions, Settings, Agents, Messages) are
installed with `native/apps/install-apps.sh <prefix>` (binaries, wrappers that source `/etc/taos/shell.env`,
`.desktop` files and icons).

**Window controls (right click on a running app, or on a Workbench taskbar button).** One list, built in one place
(`DockModel::windowControlRows`), used by the menu of an app with one window, by the submenu of each window of an app with
several (`Activity` > `Minimise`, `Maximise`, ...), and by the Workbench taskbar button's menu:

| Row | Engine command | Notes |
| --- | --- | --- |
| Minimise / Restore | `taos-window minimise` / `restore <id>` | Restore for a minimised window |
| Maximise / Restore size | `taos-window maximise <id>` / `restore <id>` | fills the usable area (bar and dock excluded), not fullscreen; waits for a window that is minimised |
| Full screen / Exit full screen | `taos-window fullscreen <id> on` / `off` | sway's real fullscreen; the container is never resized by us |
| Bring here | `taos-window bring-here <id>` | onto the focused workspace, centred, at a size that fits (comes back from minimised too) |
| Reset size | `taos-window reset-size <id>` | default size for the screen, centred on its own workspace; off for tiled windows |
| Move to space > | `taos-window move-to-space <id> <space>` / `new-space <id>` | the spaces come from `taos-window spaces --json`; the window's own space is ticked |
| Close | `taos-window close <id>` | |

The dock only calls the engine; every geometry rule lives in `native/shell/window`. An engine that cannot do a verb (the built-in
`SwayEngine` fallback) says so on stderr and does nothing. Keyboard: Menu key (or Super+D, then the arrows and Menu) opens
the menu of the focused item; Up and Down move, Right or Enter on a row with a chevron opens its submenu (Left or Escape
closes it, Escape from the first panel closes the menu), Enter runs a row. Labels match the title bar buttons and the
frame's window menu: Maximise, Full screen. `dockshot` (`--scene menu|taskbar-menu`, `--windows single|several`, `--sub`, `--light`)
renders them offscreen.

The dock follows the desktop/tiling mode: `~/.config/taos/desktop.json` (written by the window engine) and the
focused sway workspace decide it. On a tiling workspace the dock behaves as auto-hide (no reserved space)
whatever `autohide` says, unless `hideInTiling` is false; in desktop mode `autohide` from dock.json applies.

## Installing the whole desktop

`native/install-desktop.sh --sway <compositor> --shell-build <dir> --apps-build <dir>... [--root <fake root>]`
(run by root; idempotent) validates the complete sway config in a temp copy, backs up `/opt/taos/shell/bin`,
`/opt/taos/apps/bin` and `/etc/taos/sway.d`, installs the shell pieces, apps, themes and snippets, moves
`$mod+space floating toggle` to `$mod+Shift+space` (the launcher owns Super+Space), adds `taos-launcher` to
`TAOS_SHELL_COMPONENTS`, and prints a rollback command. It restarts nothing. `native/restart-desktop-pieces.sh`
(as the logged-in user, inside the session) restarts the shell components through `taos-shell-session`, starts
`taos-window mode-daemon` if it is not running, and runs `swaymsg reload` only with `--reload`.
swayfx creates its GL renderer before it reads the config, so `sway -C` needs a GPU with it; on a machine without
one, pass `--validate-with <plain sway>`.

## Session packages other toolkits depend on

Debian trixie packages the desktop session needs besides the build dependencies:

```text
apt install chromium xdg-desktop-portal xdg-desktop-portal-gtk gsettings-desktop-schemas
```

* `chromium`: web apps (`taos-webapp`).
* `xdg-desktop-portal` and `xdg-desktop-portal-gtk`: publish the colour scheme (`org.gnome.desktop.interface
  color-scheme`) to running Chromium and GTK apps, so dark and light follow taOS live. Without them a running
  Chromium reads the setting once at start; the web apps engine then adds `--force-dark-mode` to a dark launch.
* `gsettings-desktop-schemas` (and the `gsettings` tool from `libglib2.0-bin`): what `taos-window-session-hook`
  writes to. The hook publishes the taOS appearance (dark, light, auto resolved now) as `color-scheme`
  (`prefer-dark` or `prefer-light`) and `gtk-theme` (`Adwaita-dark` or `Adwaita`) at session start and on every
  change of `appearance.json`; it does nothing when `gsettings` is missing. It is started by the sway snippet
  (`exec taos-window-session-hook watch`) and by `restart-desktop-pieces.sh`.
