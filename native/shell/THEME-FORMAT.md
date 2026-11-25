# Theme package format, version 2

A theme is a folder `<id>/theme.json` plus the files it names. It is data only: no code, no scripts, no CSS. The
engine (`common/themedef.cpp`) reads it, checks every key, and either shows all of it or none of it. Unknown keys,
wrong types and out of range numbers are refused with a plain sentence; a refused theme never replaces the one
that is showing (start-up alone falls back to Graphite, because nothing is showing yet).

Version 1 themes (colours, `motion`, `windowButtons`) work unchanged. A theme without `skin`, `layout`, `options`
or the two optional colours looks exactly as before: every default below is what the shell drew before this format.

Where themes are looked up, the picker, `appearance.json` and the colour rules are in `common/THEMES.md`.
This file covers what v2 added. Folder layout of a package that uses everything:

    parallax/theme.json
    parallax/wallpaper-signal.png ...        wallpapers named by skin.wallpaper and by option choices
    parallax/fonts/ChakraPetch-Medium.ttf    font files named by skin.fonts (each family with its licence file)
    parallax/NOTICE

## Colours (added: two optional tokens)

`attention` (default: the accent) is the colour for what must not be missed: corner brackets, the keyboard focus ring
in themes that want one. `accentDim` (default: `stroke`) is the quiet form of the accent: ticks, idle borders. Both are
`#RRGGBB[AA]` like the other tokens, per variant, and may be left out.

## `skin`: how things are drawn

Every key is optional. "Obeyed by" names the pieces that read the key today; a key is only listed if something reads it.

| key | values (default) | obeyed by |
|---|---|---|
| `radiusScale` | 0 to 2 (1) | multiplies every corner radius of the bar, tray, dock, taskbar, launcher, bar menu panels and window buttons. Menu rows and the in-menu controls keep fixed radii |
| `borderWidth` | 0 to 4 px (1) | hairlines and bevels of bar, tray, taskbar, dock strip, menu panels, launcher, square window buttons |
| `shadowStrength` | 0 to 2 (1) | drop shadow of bar menu panels and of the launcher. Not the compositor's window shadows |
| `fontFamily` | one family name (the shipped Inter stack) | bar, tray, taskbar, dock labels, launcher field, bar menu frames, compositor title bars |
| `fontDisplay` | family name (the body face) | titles and marks: strip bar mark, compositor title bars |
| `fontData` | family name (the body face) | numbers and small labels: strip bar, launcher ring field |
| `fonts` | up to 12 file names `*.ttf` or `*.otf` in `fonts/` | loaded into every shell process that follows the theme when it shows. The compositor's own title font needs the same files in the person's fontconfig path, see "Not done" |
| `fontSize` | 9 to 20 px (13) | bar, tray, taskbar text; base size of compositor title bars |
| `iconStroke` | 0.75 to 3 px (1.5) | speaker, monitor and wired network glyphs; window button glyphs |
| `titleBarHeight` | 20 to 48 px (32) | compositor drawn title bars and the button overlay of `taos-frame` |
| `windowButtons` | object, below | `taos-frame` button overlay |
| `activeTitle` | `surface`, `accent` (surface) | colour of the focused window's compositor title bar |
| `titleAlign` | `center`, `left` (center) | title text in compositor title bars |
| `titleCase` | `none`, `upper` (none) | compositor title bars (pango 1.50 or newer), strip bar labels |
| `titleTracking` | 0 to 0.4 em (0) | letter spacing of compositor title bars and strip bar labels |
| `focusBorder` | `neutral`, `accent` (neutral) | border of framed windows: the focused one in the accent, the others in `accentDim` |
| `brackets` | true, false (false) | corner brackets on the focused window, drawn by `taos-frame` |
| `blur` | true, false (false) | asks the compositor to blur behind bar, tray, dock and taskbar (`layer_effects`, swayfx only) |
| `texture` | `none`, `scanlines` (none) | faint line texture on the bar and the launcher panel |
| `barSurface` | `solid`, `translucent`, `raised` (translucent) | the top bar |
| `dockSurface` | same (translucent) | dock, taskbar, tray, launcher panel |
| `density` | `compact` .75, `regular` 1, `comfortable` 1.25 | spacing of bar, tray, taskbar |
| `windowRadius` | 0 to 32 px (0) | compositor window corners, sent by `taos-frame` (swayfx only) |
| `windowGap` | 0 to 32 px (8) | gap between tiled windows, sent by `taos-frame` |
| `wallpaper` | a plain file name `*.png`, `*.jpg` in the theme folder (none) | sent to the compositor by `taos-frame`; a theme without one gives the session's own wallpaper back |

`raised` is an opaque surface with a lit top and left edge and a shaded bottom and right edge, derived from the colour
tokens. `solid` is the opaque ground colour with a hairline.

`skin.windowButtons`: `shape` `round` (disc, glyph on group hover: the original), `square` (a bevelled key, glyph always
shown) or `glyph` (bare line glyphs: a short line, a square outline, a rotated square outline in the danger colour
for close); `size` 10 to 24 px (14); `side` `left` or `right`; `order` the three names `close`, `minimise`, `full`
left to right on screen (default by side). `side` is the same setting as the top level `windowButtons` key of v1; giving
both with different values is an error. The person's own choice (`taos-theme set-buttons`) still wins.

## `layout`: which pieces exist and where

| key | values (default) | obeyed by |
|---|---|---|
| `topBar` | true, false (true) | `taos-bar` makes the top bar or not. False needs `dock` `taskbar`: the tray then takes the clock, sound, network and display |
| `barStyle` | `bar`, `strip` (bar) | `strip` is the thin instrument strip: mark, workspace ticks, network, volume as ten ticks, clock |
| `barHeight` | 24 to 64 px (40) | top bar (`--height` on the command line still wins) |
| `barEdge` | `top`, `bottom` (top) | top bar, and the side bar menus open away from |
| `clock` | `left`, `center`, `right` (right) | clock and date in the standard top bar |
| `dock` | `dock`, `taskbar`, `cells` (dock) | `taos-dock`: floating dock, full width taskbar (start button, one labelled button per window, tray at the end), or a centred row of shaped cells |
| `cellShape` | `hex`, `square` (hex) | cells of dock `cells` |
| `taskbarHeight` | 28 to 64 px (40) | taskbar and tray |
| `dockEdge` | `top`, `bottom` (bottom) | taskbar and tray |
| `startLabel` | 1 to 16 letters, digits, spaces (taOS) | text of the taskbar's start button |
| `launcher` | `centered`, `anchored`, `ring`, `text` (centered) | `taos-launcher`: which renderer draws the app menu (one shared menu and engine, see `launcher/README.md`): a full panel; the two pane start menu at the taskbar's start button (needs dock `taskbar`); a ring of pinned apps over a wide field; or `text`, a calm list of favourite names in large type with no icons |

The taskbar keeps its window buttons in the order the windows appeared; focus never reorders them. When they do not
fit, the active window stays visible and a "+N" button lists the rest.

### Launcher styles

A launcher style is a renderer of the shared app menu (pinned, recent, every app, search, the system actions): the theme only
names which one it wants, so a theme never touches the engine and cannot break it. The four values above are the styles that
ship; `layout.launcher` accepts exactly those, and anything else is refused with the list ("layout.launcher must be one of:
centered, anchored, ring, text"). Each style draws only from the theme's own tokens and skin (colours, `fontFamily`, `fontDisplay`,
`fontData`, `fontSize`, `density`, `radiusScale`, `borderWidth`, `dockSurface` bevels, `texture`). A narrow screen always gets the phone drawer, and a person or a device
can force a style with `taos-launcher daemon --style <name>` (the text style makes a good phone home screen). A style is a QML file in
the launcher (`launcher/qml/Menu<Name>.qml`) registered by the launcher itself; third party styles will arrive with the plugin system,
not through a theme folder, because a theme is data only.

## `options`: choices a person makes inside the theme

    "options": { "accent": { "label": "Accent", "default": "signal",
        "choices": { "signal": { "label": "Signal", "colors": { "accent": "#6FD3CF" }, "wallpaper": "wallpaper-signal.png" },
                     "ember":  { ... } } } }

Up to 8 options of 2 to 8 choices. A choice may replace colour tokens (every variant) and the wallpaper, nothing else.
Every choice is checked for contrast as if it were the theme: text on surface, text on ground, accent text on accent,
all at least 4.5:1. `"available": false` with a `note` marks a choice the system cannot show yet: it is accepted and
saved, the person is told, and the default keeps showing (never silently, never pretended).
A person's choices live in `appearance.json` under `options.<theme id>.<key>`.

    taos-theme options parallax                  the choices and which is picked
    taos-theme option parallax accent ember      pick one: live, saved
    taos-theme option parallax depth on          saved, reported as "not available on this system yet"

## Tools and exit codes

`taos-theme list | get | set <theme> [--mode ..] | set-buttons | options | option | validate <file or folder> | export`

Exit codes: 0 done or valid; 1 refused (unknown theme, unknown option or choice, invalid theme, cannot save);
2 wrong usage; 3 a file given to `validate` cannot be read. `validate` prints one line per problem and a count.

## Safety

* Strings that reach the compositor are checked: font families are letters, digits, spaces and dashes; wallpaper and
  font files are plain names, never paths.
* A wallpaper or font the theme names must exist in its folder, or the theme is refused.
* A theme cannot leave the shell without a bar: no top bar requires the taskbar.

## Not done (known)

* `fonts` reach the shell's own processes, not the compositor: the compositor reads fonts through the person's
  fontconfig, so title bar text uses the theme's display face only when those files are in `~/.local/share/fonts`
  at compositor start. Falls back to Inter otherwise.
* Native apps draw their own window header and their content from their build; they follow colours (live) but not
  skin keys until they are rebuilt on the shared style.
* `windowRadius`, `blur` and the compositor shadows need swayfx; plain sway ignores them.
* There is no `depth` effect yet: a choice marked unavailable says so.
