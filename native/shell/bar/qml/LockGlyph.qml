// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// A small padlock: a body and a shackle, from rectangles.
Item {
    id: g
    property color color: Theme.textSecondary
    width: 10
    height: 12
    Rectangle {
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        width: 6.5; height: 7; radius: 3.25
        color: "transparent"
        border.width: 1.4
        border.color: g.color
    }
    Rectangle {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        width: 10; height: 7; radius: 2
        color: g.color
    }
}
