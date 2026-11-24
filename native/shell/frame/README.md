# taos-frame: the window buttons for every window

Every normal window on the desktop shows the same three taOS buttons (close, minimise, full size), always drawn (not
only on hover), in the order and on the side the theme sets (`windowButtons`, the person can override it), with a
focused and an unfocused style, hover and pressed feedback, a tooltip and an accessible name, whatever toolkit drew it.

## The rule: who draws the title bar

**One rule: the compositor draws the title bar, `taos-frame` draws the three buttons on it, for every window that can
take server side decoration. A window is left out only when it draws its own header.** Apps are asked for server side
decoration (xdg-decoration; sway answers server side by default and `taos-theme export foot` sets foot to
`preferred=server`). The exceptions, in the order they are checked:

| Window | Who draws the buttons | How it is recognised |
|---|---|---|
| taOS native apps (`my.taos.*`) | the app: `TaosTitleBar` / `TitleButton` (same look as the strip) | app id, built in |
| a window that asked for client side decoration (GTK 3 with a header bar, foot with `preferred=client`, Qt with its own bar) | the app | sway reports `border csd`: detected from the protocol, nothing to configure |
| GTK 4 and libadwaita apps (they never use server side decoration) | the app | the process maps `libadwaita-1` or `libgtk-4` (`/proc/<pid>/maps`, read once per window) |
| apps that draw a header and say nothing about it (Firefox, Thunderbird, ghostty) | the app | `selfDecorating` list in `frame.json` |
| normal browser windows (`chromium`, `google-chrome*`) | the browser | built in (`border csd`) |
| everything else: web app windows (Chromium `--app`), the terminal, Qt apps, GTK 3 windows without a header bar, Electron apps, apps from a container | sway draws the bar, taos-frame draws the buttons | the default |

GTK windows that draw their own header show their own buttons; the theme's button layout
(`taos-theme export button-layout`, applied by `taos-window-session-hook`) puts them in the same order and on the same
side as everything else (`close,minimize,maximize:` on the left, `:minimize,maximize,close` on the right). They keep
GTK's own look: that is the one visible difference, and it is a GTK limit.

A window is judged once the client has settled its decoration mode (sway shows `border none` until then), at most one
second after it maps: a bar forced on a GTK 3 header bar window before it has said so stays and shows two sets.

### Kinds of window

* **Normal** window: all three buttons.
* **Dialog, utility, splash, toolbar**: close only; minimise and full size are drawn as empty, inert discs. X11 windows
  are recognised from their window type or `transient_for` (sway reports them). Wayland does not tell sway which
  toplevel is a dialog or has a fixed size, so for Wayland apps this is data: `dialogs` (close only) and `fixedSize`
  (no full size button) in `frame.json`. **Not detected automatically.** A compositor patch that exports the parent
  and the min/max size in `get_tree` would replace both lists.
* **Fullscreen** (`F11`, `Super+F`): no title bar, no buttons; they come back when it ends.
* **Minimised** windows are parked; their buttons are hidden and the dock shows them (click to restore).
* **Tabbed and stacked** containers: sway draws the tab titles, no buttons.
* **Popups, menus, tooltips** are not toplevels: never framed.

### Keyboard
The buttons never take the keyboard (the strip must not steal focus from the window). The equivalents: `Super+Q` close,
`Super+M` minimise, `Super+Shift+M` restore the last one, `Super+Ctrl+Up` full size or restore, `Super+Ctrl+Shift+Up`
fullscreen. The tooltip names them. Native apps' own buttons are focusable (ring on keyboard focus).

### Look
Disc 14 px in a 24 px hit area. Focused window: disc at 26% of the text colour, glyph at 92%. Unfocused: disc 12%,
glyph 50%. Hover: disc 46%; close turns the theme's danger colour. Pressed: 60% (close: darker danger). Unavailable
button: 8% disc, no glyph. Tooltip after 500 ms. `native/qt/style/Taos/Style/TitleButton.qml` (native apps) and
`qml/StripButton.qml` (here) are kept identical.

## Mechanism

* **sway reserves and draws the bar.** A framed window gets `border normal 1`: sway then reserves a title bar above the
  content, tiles around it, moves the window when the bar is dragged and drops it in the tiling layout. No patch.
  `taos-frame` sets sway's `client.*` colours to the theme (surface, text, a hairline), keeps the bar exactly 32 px
  (it reads the bar height from `get_tree` and corrects `font` size and `titlebar_padding`, which converges in a few
  steps whatever the installed font measures) and applies `border normal 1` to windows matching the rules.
* **taos-frame draws only the buttons**: one 80 x 24 layer-shell surface (layer top, anchored top left, margins from the
  output, exclusive zone -1, no keyboard) per framed window, over the bar's corner (8 px in, centred in the bar). Button
  side follows `windowButtons` (appearance.json) live. The surface sits over the bar's left or right corner; the title
  and the hairline are sway's.
* **Following.** One thread subscribes to sway's `window`, `workspace` and `output` events and fetches `get_tree` after
  each. Interactive moves and resizes raise **no** sway event (measured on sway 1.10 with a virtual pointer), so it also
  polls: every 100 ms when a framed window exists, every 16 ms for 400 ms after anything changed. A window whose
  geometry changed in the last 150 ms hides its buttons and they come back where it was dropped. The bar rectangle is
  taken from the content rectangle (the bar is directly above it): `deco_rect`'s position is relative to the workspace
  (measured on sway 1.10 with a notification bar reserving 33 px at the top: a tiled window at y 64 reports deco y 0, a
  floating one at 300 reports 267) while `rect` is absolute, so only its height is used. A real tree with a reserved
  top is a test fixture.
* **Hidden** when the window is fullscreen, parked (minimised), on a hidden workspace, has no bar yet, is moving, or a
  window above it covers the buttons (a layer surface is always above every window, so an overlapped window's buttons
  have to give way).
* **Actions** go through `taos_window_core` like the dock and the native header: close, minimise (scratchpad plus marks,
  `scratchpad_minimize` is never used), full size. Right click on a title bar opens a small menu (minimise, full size,
  close): `49-taos-frame.conf` binds `--release --border button3` to `taos-frame menu`; sway focuses the clicked window
  first, so "the focused window" is the one clicked. Right click on the buttons does the same.
* **Window core.** With a sway title bar `rect` is the content below the bar, but `resize set` and `move position` speak
  of the container, bar included, so `taos_window_core` replayed saved geometry 32 px lower and 32 px shorter on every
  full size or minimise cycle (measured). `window/core/windowmodel.cpp` now keeps `Window::rect` in container terms
  (bar included) when sway draws a bar; the integration test cycles full size and minimise/restore three times.
* **Tabbed and stacked containers** are not supported: sway draws their tab titles, the strips stay hidden there.
* **Tooltip.** A second, input-transparent overlay surface under the hovered button (the strip is only 80 x 24).
* **Not done:** double click on the bar for full size (sway has no double click binding; it needs the compositor
  title bars). The buttons are not focusable: keyboard users have Super+Q, Super+M and Super+Ctrl+Up, and the strip never
  takes the keyboard.

## Which windows: `~/.config/taos/frame.json`

```json
{ "include": [".*"], "exclude": [], "selfDecorating": ["^org\\.mozilla\\.", "^(firefox|thunderbird)", "^com\\.mitchellh\\.ghostty$"],
  "dialogs": [], "fixedSize": [] }
```

All keys are optional lists of regular expressions on the app id. `exclude` and `selfDecorating` both leave a window
alone (exclude wins over include); `selfDecorating` is the list Settings should offer ("this app draws its own title
bar") and a key in the file **replaces** the shipped list. `include` narrows the set (default: everything).
`frameAllForeign` is the old name for the default and is ignored in effect. Never framed whatever the file says:
`my.taos.*` and normal browser windows. The file is watched. **Upgrading:** a `frame.json` written for the old rule
(`{"include": ["^chrome-"]}`) still narrows framing to web apps; delete the `include` line to get every window.

## Control

`taos-frame menu | state | press <window-id> <close|minimise|full>` talk to the running daemon over
`$XDG_RUNTIME_DIR/taos-frame.sock`. `state` is the JSON the tests read.

## Tests

`test_frameplan` (rules, geometry from fixture trees, hide states, hit regions, tuner), `test_frameqml` (offscreen clicks,
accessible names, live theme), `test_frame_sway` (headless sway with foot: framing, geometry, follow latency, a real
dragged move with a virtual pointer, fullscreen / workspace / minimise, close and minimise act on the right window,
button side, rules file, sway reload). `frameshot out.png [--right] [--light] [--hover close|minimise|full]` renders a
framed window offscreen (the bar there is a stand-in for sway's).

## Install

`taos-shell-session` starts `taos-frame` by default; `install-desktop.sh` installs the binary and
`/etc/taos/sway.d/49-taos-frame.conf`. Without the daemon a framed window keeps a plain sway title bar (title, no
buttons): `Super+Q` closes it.

## Seeing it (headless, real config)

`tests/session/` starts a private taOS session (own HOME, runtime dir and D-Bus; `dbus-update-activation-environment`
is shimmed so nothing reaches a live session; Intel render node only) with `/etc/taos/sway/config-effects` and the
binaries of this build first on PATH (`prep.sh` makes the shim directory and a `shell.env` that
`TAOS_SHELL_ENV_FILE` points at, `run-session.sh` starts sway). `matrix.py` launches an app and takes grim shots of
every state with real pointer events (`taos-frame-vptool`): unfocused, focused, hover (tooltip), maximised, restored,
minimised, restored. Set `W` (work directory) and `QT_ENV_SH` (the Qt environment file) first.
