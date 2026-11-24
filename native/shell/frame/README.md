# taos-frame: window buttons for windows that cannot draw their own

Web app windows (Chromium `--app`, app_id `chrome-<host>__<path>-<profile>`) are xdg_shell toplevels that get no caption
from Chromium and none from the compositor. `taos-frame` gives them the taOS look: sway draws the title bar, `taos-frame`
puts the three round buttons (close, minimise, full size, as `TaosTitleBar`) over its corner.

Decision (owner): a controls strip now, compositor title bars later.

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
* **Not done:** double click on the bar for full size (sway has no double click binding; it needs the compositor
  title bars). The buttons are not focusable: keyboard users have Super+Q, Super+M and Super+Ctrl+Up, and the strip never
  takes the keyboard.

## Which windows: `~/.config/taos/frame.json`

```json
{ "include": ["^chrome-"], "exclude": [], "frameAllForeign": false }
```

`include` and `exclude` are regular expressions on the app_id (exclude wins). `frameAllForeign` frames every window that
is not excluded. Never framed whatever the file says: `my.taos.*` (own header) and normal browser windows
(`chromium`, `chromium-browser`, `google-chrome*`: sway answers them `border csd`, they draw their own frame). The file is
watched. Foot, for example: `{"include": ["^chrome-", "^foot$"]}`.

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
