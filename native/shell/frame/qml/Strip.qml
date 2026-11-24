import QtQuick
import Taos.Frame

// The three buttons of a framed window. sway draws the title bar (background, title, hairline) in the theme's colours;
// this surface sits exactly over its button corner. The geometry comes from C++ (StripLayout::buttons), so the hit
// regions the tests check are the ones drawn here.
Item {
    id: root
    property var buttons: []              // [{name, x, y, w, h, label}]
    property bool active: true
    property var bridge: null             // C++: press(button), menu()
    signal activated(string button)
    signal contextMenu()
    onActivated: (button) => { if (bridge) bridge.press(button) }
    onContextMenu: { if (bridge) bridge.menu() }

    HoverHandler { id: groupHover }
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onClicked: root.contextMenu()
    }
    Repeater {
        model: root.buttons
        StripButton {
            required property var modelData
            x: modelData.x
            y: modelData.y
            width: modelData.w
            height: modelData.h
            glyph: modelData.name
            label: modelData.label
            danger: modelData.name === "close"
            groupHovered: groupHover.hovered
            windowActive: root.active
            onActivated: root.activated(modelData.name)
        }
    }
}
