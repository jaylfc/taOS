// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Style 1.0

// A card surface: the theme surface and one hairline (elevation is declared
// once, as the hairline: no shadow, no sheen).
Rectangle {
    id: g
    radius: Pal.radius
    color: Pal.glassFill
    border.width: 1
    border.color: Pal.hairline
}
