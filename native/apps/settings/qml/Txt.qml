// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Style 1.0

// Text in the app's voice: Inter, tabular numerals, theme colours.
Text {
    color: TaosStyle.text
    font.family: "Inter"
    font.pixelSize: TaosStyle.bodySize
    font.features: ({ "tnum": 1 })
    wrapMode: Text.Wrap
    renderType: Text.NativeRendering
}
