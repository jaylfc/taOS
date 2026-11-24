// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// One choice of a small group (a scale preset, a refresh rate): a pill that is
// filled when it is the current one. Announced as a radio button with its state.
// Enter or Space picks it; Left and Right move to the neighbouring choice.
Item {
    id: chip
    property string text: ""
    property bool checked: false
    property string accessibleName: text
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    signal activated()

    implicitWidth: label.implicitWidth + 18
    implicitHeight: 28
    width: implicitWidth
    height: implicitHeight
    activeFocusOnTab: true

    Accessible.role: Accessible.RadioButton
    Accessible.name: chip.accessibleName
    Accessible.checkable: true
    Accessible.checked: chip.checked
    Accessible.focusable: true
    Accessible.onPressAction: chip.activated()
    Accessible.onToggleAction: chip.activated()

    function step(d) {
        var kids = chip.parent ? chip.parent.children : []
        var i = -1
        for (var k = 0; k < kids.length; ++k) if (kids[k] === chip) i = k
        for (var j = i + d; j >= 0 && j < kids.length; j += d)
            if (kids[j].activeFocusOnTab === true && kids[j].visible) { kids[j].forceActiveFocus(); return }
    }

    HoverHandler { id: hover }
    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: tap.pressed ? Theme.pressed : chip.checked ? Theme.pressed : hover.hovered ? Theme.hover : "transparent"
        border.width: 1
        border.color: chip.checked ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.55) : Theme.stroke
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 100 } }
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: -2.5
        radius: height / 2
        color: "transparent"
        border.width: 1.5
        border.color: Theme.text
        opacity: chip.activeFocus ? 0.4 : 0
    }
    Text {
        id: label
        anchors.centerIn: parent
        text: chip.text
        color: chip.checked ? Theme.text : Theme.textSecondary
        font.family: chip.fontFamily
        font.pixelSize: 12
        font.weight: chip.checked ? Font.DemiBold : Font.Medium
        font.features: { "tnum": 1 }
        renderType: Text.NativeRendering
        Accessible.ignored: true
    }
    MouseArea { id: tap; anchors.fill: parent; onClicked: chip.activated() }
    Keys.onReturnPressed: chip.activated()
    Keys.onEnterPressed: chip.activated()
    Keys.onSpacePressed: chip.activated()
    Keys.onLeftPressed: chip.step(-1)
    Keys.onRightPressed: chip.step(1)
}
