// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Frame

// One round window button, as TitleButton in the foundation: monochrome 14 px disc in a 24 px hit area; the glyph
// shows while the pointer is over the group of three. `danger` turns the disc to the theme's danger colour on hover.
// Kept in step with native/qt/style/Taos/Style/TitleButton.qml (the strip cannot import the foundation module).
Item {
    id: btn
    property string glyph: "close"        // close | minimise | full
    property string label: ""
    property bool groupHovered: false
    property bool danger: false
    property bool windowActive: true
    signal activated()

    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: btn.activated()
    readonly property bool hovered: hover.hovered

    Rectangle {
        id: disc
        anchors.centerIn: parent
        width: 14
        height: 14
        radius: 7
        color: btn.hovered && btn.danger ? Theme.danger
             : (btn.hovered || mouse.pressed ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, mouse.pressed ? 0.42 : 0.34)
                                             : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, btn.windowActive ? 0.22 : 0.12))
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 90 } }
        Item {
            anchors.centerIn: parent
            width: 8
            height: 8
            visible: btn.groupHovered
            readonly property color ink: btn.hovered && btn.danger ? Theme.ground : Theme.text
            Rectangle { visible: btn.glyph === "close"; anchors.centerIn: parent; width: 9; height: 1.4; rotation: 45; color: parent.ink; antialiasing: true }
            Rectangle { visible: btn.glyph === "close"; anchors.centerIn: parent; width: 9; height: 1.4; rotation: -45; color: parent.ink; antialiasing: true }
            Rectangle { visible: btn.glyph === "minimise"; anchors.centerIn: parent; width: 8; height: 1.4; color: parent.ink }
            Rectangle {
                visible: btn.glyph === "full"
                anchors.centerIn: parent
                width: 7
                height: 7
                color: "transparent"
                border.width: 1.4
                border.color: parent.ink
            }
        }
    }
    HoverHandler { id: hover; cursorShape: Qt.ArrowCursor }
    MouseArea {
        id: mouse
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: btn.activated()
    }
}
