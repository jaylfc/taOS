# Third party material in native/shell

No icon set, font or other asset is bundled. The bar's glyphs (network,
volume, battery) are drawn from plain QML rectangles, and the text uses the
system font (`Inter`, then `Noto Sans`, then `DejaVu Sans`) found at runtime.

Link-time dependencies, all LGPL-3.0 and dynamically linked:

| Component | Licence | Use |
|---|---|---|
| Qt (Core, Gui, Qml, Quick, WaylandClient; Test for tests only) | LGPL-3.0 | UI toolkit |
| LayerShellQt | LGPL-3.0-or-later | layer-shell surfaces |
