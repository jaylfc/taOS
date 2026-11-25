# taos-window: window controls for the taOS native desktop

`taos-window` is the window engine: the one place that knows how to list, close, minimise, restore, focus and
resize windows on sway. The dock, the overview, the title bar of every taOS app (`TaosWindow` in native/qt) and
the keyboard shortcuts all use it, through the static library `taos_window_core` or through the command.

    taos-window list [--json]
    taos-window close|minimise|restore|toggle-minimise|focus|full|fullscreen <id|focused|last>
    taos-window subscribe          one JSON line per change: {"event": new|close|focus|title|minimise|restore, "window": {...}}

`list --json` gives `[{id, app_id, title, pid, workspace, focused, minimised, floating, fullscreen, output}]`, minimised
windows included (`workspace` and `output` are where they will come back to; `floating` is how they will come back).
`<id>` is the sway container id, `focused` the focused window, `last` (restore, focus) the most recently minimised one.
Exit status: 0 done, 1 refused (a plain sentence on stderr), 2 usage, 3 not running under sway.

## Minimise: the scratchpad plus marks

Sway has no minimise. A minimised window is parked in sway's scratchpad and carries marks, so the state lives in
the compositor and survives the death of every taos process (the integration test kills a `subscribe` with SIGKILL
and restores from a fresh process):

| mark | meaning |
| --- | --- |
| `taos_min_<conid>` | minimised |
| `taos_at_<ms>` | when (orders "restore the last one") |
| `taos_ws_<name>` | the workspace it came from (percent-encoded) |
| `taos_mfl`, `taos_geo_<x>_<y>_<w>_<h>` | it was floating, and where |
| `taos_mfs` | it was fullscreen |

Two mechanisms were tried on a headless sway 1.10.1 with one tiled and one floating window:

* Scratchpad (chosen). Floating geometry comes back exactly (sway keeps it); a tiled window comes back floating
  and `floating disable` puts it back in the tiling tree (it loses its slot, not its workspace). The scratchpad is
  skipped by `workspace next/prev`, the bar and the overview's workspace list, and sway already treats it as hidden.
* A hidden workspace `__taos_min`: a tiled window keeps its tiled state, but `workspace next` walks into the
  hidden workspace (shown: it became the focused one), and every workspace list in the bar, the overview and any
  other client would have to learn to skip it. Rejected.

Restore goes back to the original workspace (switching to it), restores tiled or floating, re-enters fullscreen
and focuses the window. Marks are removed. A window hidden in the scratchpad by someone else is listed as
minimised too, and restoring it shows it on the current workspace.

## Full size

Both kinds of window are made to fill the workspace's usable area (the output minus the bar and the dock; sway's
workspace rectangle already excludes layer-shell reserved space). Sway's `fullscreen` is not used for this: it
hides the bar and the dock, which is not what the green button of a window with a dock means. A tiled window is
floated and sized to the area (`taos_ftiled`); toggling again tiles it back. A floating window remembers its
geometry (`taos_fgeo_...`) and goes back to it. `taos_full` is the flag. Real fullscreen is a separate verb,
`taos-window fullscreen`, bound to Super+Shift+Up. The state survives minimise and restore.

## Desktop and tiling mode

    taos-window mode get|tiling|desktop|toggle [--workspace <name>]     (default: the focused workspace)
    taos-window mode list [--json]          [{"name": "1", "mode": "desktop"}, ...]  for every workspace
    taos-window mode-daemon                 always on; the snippet starts it with `exec`

The window style is per workspace and per person: `~/.config/taos/desktop.json`
(`$TAOS_DESKTOP_CONFIG`), written atomically,

    {"default": "desktop", "workspaces": {"2": "tiling"}}

A person with no file gets desktop mode everywhere.

* desktop: tiled windows on the workspace float and keep their on-screen geometry; new windows open floating at 60 %
  by 70 % of the usable area, centred and cascaded by 32 px for each floating window already there.
* tiling: floating windows are tiled; new windows tile (sway's default). Minimised windows are left alone.
* The dock follows by reading `desktop.json` or the `mode` events of `taos-window subscribe`
  (`{"event":"mode","workspace":"1","mode":"tiling"}`), which fire whoever changed the file.
* New windows are handled by `taos-window mode-daemon` (a small process that follows sway's window events), not by
  a sway `for_window` rule, which cannot depend on the workspace's current mode. A client may undo the first
  resize, so the helper (and the switch) re-applies the geometry until it holds. Windows that float by themselves
  (dialogs) are left as they are. Per-app remembered sizes are not done yet.
* No on-screen notice: the bar's OSD is volume-only and has no general entry point.

## Other apps (first pass, no compositor patch)

* **Button side for GTK.** GTK 3 windows with a header bar tell the compositor they draw their own header and are left to
  it (taos-frame draws nothing on them); GTK 4 and libadwaita apps are found from the process. For those the key below
  puts their buttons in the same order and on the same side as everything else. `session/taos-window-session-hook` sets `org.gnome.desktop.wm.preferences button-layout`
  from the theme and the person's choice (`taos-theme export button-layout`: `close,minimize,maximize:` on the left,
  `:minimize,maximize,close` on the right). `watch` mode re-applies whenever `appearance.json` changes; the snippet
  starts it. GTK apps that draw their own buttons follow it after a restart of the app (GTK reads the key at start;
  some versions follow it live).
* **foot.** `taos-theme export foot` prints foot's `[csd]` section with `preferred=server`: foot asks the compositor for
  its title bar and taos-frame draws the taOS buttons on it (close, minimise, full size, on the theme's side), like
  every other window; foot no longer draws a header of its own (it only ever offered a close button, because sway does
  not advertise minimise and maximise to it, and could not put it on the left). The colours in the section are the
  theme's and only matter if a compositor refuses server side decoration. The hook writes it to
  `~/.config/foot/taos-csd.ini` and creates `foot.ini` with an `include=` of it when there is no `foot.ini`. A
  `foot.ini` that exists is not edited: the hook prints the one line to add. See `shell/frame/README.md` for the rule.
* **Minimise from other apps.** Apps that draw their own minimise button send `xdg_toplevel.set_minimized`, which
  plain sway ignores and swayfx ignores too unless `scratchpad_minimize yes` is set. **Do not turn that option on
  with `swaymsg reload`, and do not ship it in a snippet.** In swayfx 0.6.1 `view_map()` (`sway/tree/view.c`)
  registers the foreign-toplevel minimise listener only when the option is on at map time, but
  `handle_foreign_destroy()` removes it whenever the option is on at destroy time. Windows mapped before a reload
  that enables it then crash the compositor when they close (`wl_list_remove` on a never-initialised list link,
  fault address 0x8): this took the live session down on 2026-11-22. Switching it off by reload leaves a dangling
  listener. Until swayfx is fixed (guard the removal with `view->foreign_minimize.notify`, as `xdg_shell.c` does for
  its own minimise listener) the option stays off, and minimise from other apps is not supported.
  If a window is nevertheless parked in the scratchpad by someone else (a panel's foreign-toplevel request, a
  hand-typed `move scratchpad`), `taos-window mode-daemon` adopts it: it sees a window that was on a workspace a
  moment ago go to the scratchpad and adds `taos_min_*`, `taos_ws_*` and `taos_mfl` from the earlier snapshot
  (tested on real sway with `move scratchpad`), so it lists, restores to the same workspace, tiled again, and shows
  in the overview like any other minimised window. Without the daemon it still lists as minimised and restores onto
  the current workspace, floating.

### Follow-up: compositor patch plan

Not needed for the above, except the one-line swayfx crash fix described under "Minimise from other apps". If the adoption by the helper proves too late (a window is visible in the scratchpad
state for the tens of milliseconds before the helper marks it) or the daemon is not running, make the compositor do
the marking itself, in the carried build (`native/compositor`, swayfx 0.6.1, sway 1.12 base):

1. `sway/desktop/xdg_shell.c`, `handle_request_minimize()`, and `sway/tree/view.c`, `handle_foreign_minimize()`
   (the branch `event->minimized`): before `root_scratchpad_add_container()` / `root_scratchpad_hide()`, run the
   same marking as `Plans::minimise` with `execute_command()` (`sway/commands.h`): `[con_id=N] mark --add taos_min_N`,
   `taos_at_<ms>`, `taos_ws_<encoded workspace>`, and for a floating container `taos_mfl` and `taos_geo_x_y_w_h`.
   Build the strings in one new helper `view_taos_mark_minimised(struct sway_view *)` in `sway/tree/view.c` so both
   callers share it.
2. The unminimise branch of `handle_foreign_minimize()` (`root_scratchpad_show()`) should go to the marked workspace
   and clear the marks: a helper `view_taos_restore(view)` that `workspace`-switches (or moves the container) to the
   `taos_ws_` mark, runs `floating disable` when `taos_mfl` is absent, and unmarks.
3. `sway/desktop/xwayland.c`, `handle_request_minimize()` has the same scratchpad code for X11 windows: call the same helper.
4. Fix `handle_foreign_destroy()` first (guard the removal with `.notify`); only then may `scratchpad_minimize` be
   enabled, and only in the base config at session start. Rebuild with `native/compositor/build.sh`.
Until then the helper path above is the supported one.

## Chromium windows and web apps

Measured against a patched headless swayfx with Chromium 156 (`--ozone-platform=wayland`, temp profile, WAYLAND_DEBUG):
Chromium always sends `zxdg_toplevel_decoration_v1.set_mode(server_side)`, whatever `browser.custom_chrome_frame` says,
and obeys the compositor's answer. So a client "request for client-side decorations" never arrives; the compositor has
to answer `client_side`. No patch is needed for that: sway's own `border csd` does it (`view_set_csd_from_server`), also
after the window is mapped (Chromium reconfigures within 20 ms). `46-taos-window.conf` applies it to normal browser
windows (`app_id` chromium, chromium-browser, google-chrome*): they then draw their own tab strip and close button. Their own minimise goes through the minimise path
(`scratchpad_minimize`, see the compositor patch) and is restored with `taos-window restore`.

Not solved: `--app` windows (the web apps, app_id `chrome-<id>-<profile>`) draw no caption bar in Chromium 156
even when the compositor answers client-side from the first configure (forced in a test build, both with
`custom_chrome_frame` true), so there is nothing for the compositor to enable. A compositor-drawn title bar with
buttons for those windows would take about 500 lines in swayfx: the title bar scene nodes and their button
textures in `sway/tree/container.c` (`container_update_title_textures`, `container_arrange_title_bar`), hit
testing of the three buttons in `sway/input/cursor.c` (`cursor_handle_button`, using `container->title_bar` /
`deco_rect`), a config command for the button side and colours (`sway/commands/`, `sway/config.c`), the same
`scratchpad` hide for minimise, and a test. The cheaper route for web apps is Chromium's own web-app frame (an
installed app with `--app-id`, or Window Controls Overlay), or a layer-shell controls strip. The strip is built: `taos-frame` (native/shell/frame) gives them a sway title bar with taOS buttons over it.

## Install

* `taos-window` on PATH (`bin/`), `46-taos-window.conf` to `/usr/share/taos/sway/` and an `include` of it in the sway
  config (it also starts `taos-window mode-daemon`). Nothing is written to `/etc`.
* The keys: Super+M minimise or restore the focused window, Super+Shift+M restore the last minimised window,
  Super+Ctrl+Up full size, Super+Ctrl+Shift+Up fullscreen, Super+Shift+T desktop/tiling for the current workspace. Close is
  `$mod+q` (kill) in the base config and is not bound here. If `$mod+Shift+t` is already taken there, change that line.
  Reserved by the base config, not used by any snippet: `$mod+q`, f, space, the arrows and Shift+arrows, 1-9 and Shift+1-9,
  Return, d (`$mod+Tab` is the overview's). `$mod+m` is free. `tests/check-sway-config.sh` (ctest `sway-config`) loads the snippets together with a
  stand-in for the base config's keys (or the real one, `TAOS_BASE_SWAY_CONFIG=<file>`) in sway and fails on any
  config error or duplicate binding (sway only warns about those, with a red bar).
* The snippet also sets `show_marks no`.

## Tests

`test_windowcore` (logic on fixture trees that are real sway `get_tree` output) and `test_window_sway`, which starts
a headless sway with its own `XDG_RUNTIME_DIR`, opens two real Wayland windows (`taos-window-dummy`) and drives the
real `taos-window`. It needs a sway binary: `TAOS_TEST_SWAY=/path/to/sway` (default: `sway` on PATH) and
`TAOS_TEST_SWAY_LIBS=dir:dir` when it is not installed; without sway it skips with a message. The Qt Wayland
platform plugin must be loadable (`QT_PLUGIN_PATH`).


## Where new windows open

`taos-window mode-daemon` places every new floating window (a taOS app sway floated at map time, a dialog, a tool
window, an Electron or GTK app that asked for a size, a tiled window being floated on a desktop workspace): never more
than 90 % of the usable area (bar and dock excluded) in either direction, always fully inside it, then in order:
the app's remembered rectangle for this output while it still fits (`~/.config/taos/window-geometry.json`, written when
a window closes), a dialog (a smaller window of a process that already has one open on this workspace) centred on that
window, 32 px down and right of the last window of the same app, else centred. With no size asked for the default is
60 % x 70 % of the usable area, at most 1600 x 900. `tests/e2e-placement.sh` checks it under a headless sway with a bar.
The remembered rectangle is the window's place at the last window or focus event before it closed, so a move made
just before closing, with no event after it, is not kept.

Keys: `Super` + left drag moves, `Super` + right drag resizes from anywhere in a floating window (`floating_modifier`),
`Super+Alt+arrows` resize by 40 px. Not built yet: larger invisible grips on edges and corners, and snapping to screen
halves and quarters with a preview (both need a pointer-tracking surface in `taos-frame`).
