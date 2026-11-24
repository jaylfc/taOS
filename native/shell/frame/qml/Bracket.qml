// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Frame

// One corner bracket of the focused window: two 2 px strokes, 14 px long, in the attention colour. `corner` is
// "tl" (top left) or "br" (bottom right). The surface is 16 px square and takes no input.
Item {
    id: b
    property string corner: "tl"
    readonly property int len: 14
    readonly property int stroke: 2
    // horizontal stroke
    Rectangle {
        color: Theme.attention
        width: b.len
        height: b.stroke
        x: b.corner === "tl" ? 0 : b.width - b.len
        y: b.corner === "tl" ? 0 : b.height - b.stroke
    }
    // vertical stroke
    Rectangle {
        color: Theme.attention
        width: b.stroke
        height: b.len
        x: b.corner === "tl" ? 0 : b.width - b.stroke
        y: b.corner === "tl" ? 0 : b.height - b.len
    }
}
