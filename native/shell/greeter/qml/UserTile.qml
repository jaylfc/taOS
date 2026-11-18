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

    width: Math.max(diameter + 56 * Plan.s, 190 * Plan.s)
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
        width: tile.diameter + 12 * Plan.s; height: width
        x: (tile.width - width) / 2; y: -6 * Plan.s
        radius: width / 2
        color: "transparent"
        border.width: Math.max(2, 2.5 * Plan.s)
        border.color: Style.text
        opacity: tile.selected ? 0.9 : 0
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
            shadowVerticalOffset: 8 * Plan.s
        }
    }
    Text {
        id: nameText
        anchors.top: avatar.bottom
        anchors.topMargin: 14 * Plan.s
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(implicitWidth, tile.width - 8 * Plan.s)
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignHCenter
        text: tile.displayName
        color: Style.text
        font.family: "Inter"
        font.weight: tile.selected ? Font.DemiBold : Font.Medium
        font.pixelSize: (tile.selected ? 24 : 21) * Plan.s
        Behavior on font.pixelSize { enabled: !Style.reduceMotion; NumberAnimation { duration: 200 } }
        renderType: Text.QtRendering
    }
    // Under the selected name: says whose password this is, and that it is the
    // computer's own account (not an email or an online one).
    Text {
        anchors.top: nameText.bottom
        anchors.topMargin: 2 * Plan.s
        anchors.horizontalCenter: parent.horizontalCenter
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
        text: qsTr("Account on this computer")
        color: Style.textSecondary
        opacity: tile.selected ? 1 : 0
        Behavior on opacity { enabled: !Style.reduceMotion; NumberAnimation { duration: 220 } }
        font.family: "Inter"; font.weight: Font.Medium
        font.pixelSize: 15 * Plan.s
        Accessible.ignored: !tile.selected
    }
}
