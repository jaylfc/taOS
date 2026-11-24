// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// One item of the bar: a rounded hit area with hover and pressed states.
// Non-interactive items (state read-outs) keep the same metrics but no states.
Item {
    id: item
    property bool interactive: false
    property string accessibleName: ""
    property string accessibleHint: ""
    property int accessibleRole: interactive ? Accessible.Button : Accessible.StaticText
    property int hPad: 8
    default property alias content: row.data
    property alias spacing: row.spacing
    signal activated()

    implicitHeight: parent ? parent.height : 28
    implicitWidth: row.implicitWidth + 2 * hPad
    width: implicitWidth
    height: implicitHeight

    Accessible.role: accessibleRole
    Accessible.name: accessibleName
    Accessible.description: accessibleHint
    Accessible.onPressAction: if (interactive) item.activated()

    Rectangle {
        id: bgRect
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.right: parent.right
        height: Math.min(parent.height - 8, 28)
        radius: Math.round((8) * Theme.radiusScale)
        color: !item.interactive ? "transparent"
               : area.pressed ? Theme.pressed
               : area.containsMouse ? Theme.hover : "transparent"
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 120 } }
    }
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
    }
    MouseArea {
        id: area
        anchors.fill: parent
        enabled: item.interactive
        hoverEnabled: item.interactive
        cursorShape: Qt.ArrowCursor
        onClicked: item.activated()
    }
}
