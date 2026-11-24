// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Frame

// One round window button, as TitleButton in the foundation: monochrome 14 px disc in a 24 px hit area. The glyph is
// always drawn (focused window: strong, unfocused: dim); hover and pressed lift the disc. `danger` turns the disc to the
// theme's danger colour on hover. A button the window cannot use (full size on a dialog) is an inert, empty disc.
// Kept in step with native/qt/style/Taos/Style/TitleButton.qml (the strip cannot import the foundation module).
Item {
    id: btn
    property string glyph: "close"        // close | minimise | full
    property string label: ""
    property bool groupHovered: false      // kept for callers; the glyph no longer waits for it
    property bool danger: false
    property bool usable: true
    property bool windowActive: true
    signal activated()

    Accessible.role: Accessible.Button
    Accessible.name: label
    Accessible.onPressAction: btn.activated()
    readonly property bool hovered: hover.hovered && usable
    readonly property bool pressed: mouse.pressed && usable

    Rectangle {
        id: disc
        anchors.centerIn: parent
        width: 14
        height: 14
        radius: 7
        color: !btn.usable ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.08)
             : btn.hovered && btn.danger ? (btn.pressed ? Qt.darker(Theme.danger, 1.2) : Theme.danger)
             : (btn.hovered || btn.pressed ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, btn.pressed ? 0.60 : 0.46)
                                           : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, btn.windowActive ? 0.26 : 0.12))
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 90 } }
        Item {
            anchors.centerIn: parent
            width: 8
            height: 8
            visible: btn.usable
            readonly property color ink: btn.hovered && btn.danger ? Theme.ground
                : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, btn.windowActive || btn.hovered ? 0.92 : 0.5)
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
    onHoveredChanged: btn.hoverChanged(hovered)
    signal hoverChanged(bool over)
    MouseArea {
        id: mouse
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: if (btn.usable) btn.activated()
        onPressed: btn.hoverChanged(false)
    }
}
