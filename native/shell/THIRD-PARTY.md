# Third party material in native/shell

No icon set, font or other asset is bundled. The login screen's weather glyphs, avatars and buttons are drawn in code, and its type is the system's `Inter` (SIL OFL 1.1, not shipped here). The bar's glyphs (network,
volume, battery) are drawn from plain QML rectangles, and the text uses the
system font (`Inter`, then `Noto Sans`, then `DejaVu Sans`) found at runtime.

Link-time dependencies, all LGPL-3.0 and dynamically linked:

| Component | Licence | Use |
|---|---|---|
| Qt (Core, Gui, Qml, Quick, WaylandClient; Test for tests only) | LGPL-3.0 | UI toolkit |
| LayerShellQt | LGPL-3.0-or-later | layer-shell surfaces |
| Qt DBus, Qt Network, Qt Quick Shapes and Effects | LGPL-3.0 | login screen (AccountsService, greetd socket, drawing) |

Data (not shipped): the weather text the login screen shows comes from
[Open-Meteo](https://open-meteo.com), fetched by `weather/taos-weather-cache`
only when the owner opts in. Open-Meteo data is offered under CC BY 4.0; the
free API is for non-commercial use, so a commercial distribution needs their
subscription or its own weather source.
