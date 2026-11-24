// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Effects
import Taos.Greeter

// One entry of the user selector: avatar, name, selection and focus rings.
FocusScope {
    id: tile
    property string username: ""
    property string displayName: ""
    property string iconSource: ""
    property string initials: ""
    property color colorA
    property color colorB
    property bool selected: false
    property real diameter: 96
    signal activated()

    width: Math.max(diameter + 56 * Plan.s, 150 * Plan.s)
    height: Plan.tileH
    activeFocusOnTab: !selected
    scale: hover.hovered && !selected && !Style.reduceMotion ? 1.04 : 1.0
    Behavior on scale { enabled: !Style.reduceMotion; NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

    Accessible.role: Accessible.Button
    Accessible.name: tile.displayName
    Accessible.description: qsTr("Choose this user to sign in")
    Accessible.focusable: true
    Accessible.onPressAction: tile.activated()

    Keys.onReturnPressed: tile.activated()
    Keys.onEnterPressed: tile.activated()
    Keys.onSpacePressed: tile.activated()

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: { tile.forceActiveFocus(); tile.activated(); } }

    // Selection ring (not colour alone: also a size change and the name weight).
    Rectangle {
        id: ring
        width: tile.diameter + 8 * Plan.s; height: width
        x: (tile.width - width) / 2; y: -4 * Plan.s
        radius: width / 2
        color: "transparent"
        border.width: Math.max(1.5, 1.5 * Plan.s)
        border.color: Style.text
        // A lone account needs no ring: there is nothing to tell it apart from.
        opacity: tile.selected && Users.count > 1 ? 0.9 : 0
        scale: tile.selected ? 1 : 0.92
        Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 180 } }
        Behavior on scale { enabled: !Style.reduceMotion; NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
    }
    // Keyboard focus ring: accent, offset, only when focus came from the keyboard.
    Rectangle {
        width: tile.diameter + 20 * Plan.s; height: width
        x: (tile.width - width) / 2; y: -10 * Plan.s
        radius: width / 2
        color: "transparent"
        border.width: Math.max(3, 3 * Plan.s)
        border.color: Style.accent
        visible: tile.activeFocus && !tile.selected && Caps.keyboardUsed
    }
    Avatar {
        id: avatar
        x: (tile.width - width) / 2
        diameter: tile.diameter
        source: tile.iconSource
        initials: tile.initials
        colorA: tile.colorA
        colorB: tile.colorB
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "black"
            shadowOpacity: Style.dark ? 0.45 : 0.22
            shadowBlur: 1.0
            shadowVerticalOffset: 4 * Plan.s
        }
    }
    Text {
        id: nameText
        anchors.top: avatar.bottom
        anchors.topMargin: 10 * Plan.s
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(implicitWidth, tile.width - 8 * Plan.s)
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignHCenter
        text: tile.displayName
        color: Style.text
        font.family: "Inter"
        font.weight: Font.DemiBold
        font.pixelSize: 16 * Plan.s
        font.letterSpacing: -0.1 * Plan.s
        renderType: Text.QtRendering
    }
}
