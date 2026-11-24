// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Frame

// The tooltip under a window button: its own small layer-shell surface, sized to the text.
Item {
    id: root
    property string text: ""
    readonly property real wantedWidth: label.implicitWidth + 16
    readonly property real wantedHeight: label.implicitHeight + 10
    Accessible.ignored: true
    Rectangle {
        anchors.fill: parent
        radius: 6
        color: Theme.raised
        border.width: 1
        border.color: Theme.hairline
        Text {
            id: label
            anchors.centerIn: parent
            text: root.text
            color: Theme.text
            font.pixelSize: 12
        }
    }
}
