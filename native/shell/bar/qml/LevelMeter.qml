// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// The live microphone level: a row of small bars that fill from the left.
Item {
    id: m
    property real level: 0   // 0..1
    property bool muted: false
    readonly property int bars: 24
    height: 8
    Accessible.role: Accessible.ProgressBar
    Accessible.name: qsTr("Microphone level")
    Accessible.description: m.muted ? qsTr("muted") : qsTr("%1 percent").arg(Math.round(m.level * 100))
    Row {
        anchors.fill: parent
        spacing: 2
        Repeater {
            model: m.bars
            Rectangle {
                width: (m.width - (m.bars - 1) * 2) / m.bars
                height: m.height
                radius: 1.5
                readonly property bool lit: !m.muted && (index + 0.5) / m.bars <= m.level
                color: lit ? (index > m.bars * 0.85 ? Theme.warning : Theme.text) : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.16)
                Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 60 } }
            }
        }
    }
}
