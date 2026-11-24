// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Style 1.0

// Text with the app's defaults: Inter, theme colour, tabular figures.
Text {
    font.family: Pal.family
    font.pixelSize: 13
    font.features: { "tnum": 1 }
    color: TaosStyle.text
    renderType: Text.QtRendering
    elide: Text.ElideRight
}
