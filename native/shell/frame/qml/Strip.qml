// SPDX-License-Identifier: AGPL-3.0-or-later
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
    // The tooltip lives in its own surface (this one is 80 x 24): tell C++ which button is under the pointer.
    signal tipWanted(string button, string text, real centerX)
    onActivated: (button) => { if (bridge) bridge.press(button) }
    onContextMenu: { if (bridge) bridge.menu() }
    onTipWanted: (button, text, cx) => { if (bridge) bridge.tip(button, text, cx) }

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
            usable: modelData.enabled !== false
            onActivated: root.activated(modelData.name)
            onHoverChanged: (over) => root.tipWanted(over ? modelData.name : "", modelData.tip, modelData.x + modelData.w / 2)
        }
    }
}
