import QtQuick
import Taos.Bar

// Four ascending bars; the lit ones show the signal (0..100).
Item {
    id: g
    property int strength: 0
    property color color: Theme.text
    readonly property int lit: strength <= 0 ? 0 : Math.max(1, Math.min(4, Math.ceil(strength / 25)))
    width: 16
    height: 14
    Row {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 1.5
        Repeater {
            model: 4
            Rectangle {
                width: 2.5
                height: 4 + index * 3
                anchors.bottom: parent.bottom
                radius: 1
                color: g.color
                opacity: index < g.lit ? 1 : 0.28
            }
        }
    }
}
