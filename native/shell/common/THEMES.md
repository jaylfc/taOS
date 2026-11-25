# taOS themes

A theme is a folder `<id>/theme.json`. The shell, the menus, the overview, the greeter and every native
app read the same theme, so a theme recolours everything at once, live.

## Where themes live

Order of precedence for an id, first match wins:

1. Shipped inside every binary (`graphite`, `graphite-ice`, `graphite-warm`). A third-party theme can never replace these.
2. `$TAOS_THEME_DIR` (development and tests).
3. The person's themes: `~/.local/share/taos/themes/<id>/theme.json` (`$XDG_DATA_HOME` is honoured). The greeter does not read these.
4. System themes: `/usr/share/taos/themes/<id>/theme.json`.

Themes in 3 and 4 survive updates. Adding or removing one shows up in the picker without a restart.

## The choice

`~/.config/taos/appearance.json`, written atomically:

    {"theme": "graphite", "mode": "dark", "windowButtons": "left"}   // windowButtons is optional

`mode` is `dark`, `light` or `auto`. `auto` is light from 07:00 to 19:00 local time (TODO: sunrise and sunset).
A saved theme that no longer exists, or is refused, shows graphite; the file is left as it is.
A new person starts from `/etc/taos/appearance.json` if it exists, else graphite dark.

`taos-theme list [--json]`, `taos-theme get [--json]`, `taos-theme set <theme> [--mode dark|light|auto]`,
`taos-theme set-buttons left|right|theme`
do the same from a terminal or over SSH; running components pick the change up at once.

The greeter shows `/etc/taos/appearance.json` (else graphite dark) and, for the selected person, only
`/var/lib/taos/appearance/<uid>.json`, a world-readable copy that person's session keeps up to date when
that folder exists and is writable by them (for example mode 1777). It never reads a home folder.

## theme.json

    {
      "id": "my-theme",                 // lowercase letters, digits, dashes; must equal the folder name
      "name": "My Theme",               // shown in the picker
      "description": "One line.",       // optional
      "variants": ["dark", "light"],    // one or both
      "swatches": {"dark": ["#ground", "#surface", "#accent"], "light": [...]},   // optional; default: ground, surface, accent
      "colors": { "dark": { ...tokens... }, "light": { ...tokens... } },
      "motion": {"reduce": false},      // optional
      "windowButtons": "left"           // optional: "left" (default) or "right", see below
    }

`windowButtons` is the side of the window buttons on taOS app windows: `"left"` puts them close, minimise, maximise
at the left of the title bar; `"right"` puts them minimise, maximise, close at the right. Anything else refuses the
theme. A person can override it for every theme with `"windowButtons"` in `appearance.json`
(`taos-theme set-buttons left|right|theme`; `theme` removes the override). The change shows at once in every open window.

Every token is required for every listed variant, as `#RRGGBB` or `#RRGGBBAA` (CSS order, alpha last):
`ground surface raised stroke hairline text textSecondary textTertiary accent accentText warning danger ok
series1 series2 selection focusRing live track scrollbar`.

## Refused themes

A theme is refused, with a plain message (shown by `taos-theme list`, and in the bar's log), when: the JSON is
invalid, the id is malformed or differs from the folder, the name is missing, a token is missing or not hex, or
the contrast is under 4.5:1 for text on surface, text on ground, or accentText on accent.
The glass, scrim and highlight colours are derived from these tokens; they are not separate settings.
