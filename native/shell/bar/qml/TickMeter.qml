// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// A level as a row of small ticks (the strip bar's volume): `segments` ticks, the first `level` of them lit.
Row {
    id: meter
    property int segments: 10
    property real value: 0            // 0..1
    property bool dim: false          // muted: the lit ticks are shown dimmed
    readonly property int lit: Math.round(value * segments)
    spacing: 2
    Accessible.ignored: true
    Repeater {
        model: meter.segments
        Rectangle {
            width: 3
            height: 11
            color: index < meter.lit ? (meter.dim ? Theme.textTertiary : Theme.accent) : Theme.accentDim
            opacity: index < meter.lit ? 1 : 0.45
        }
    }
}
