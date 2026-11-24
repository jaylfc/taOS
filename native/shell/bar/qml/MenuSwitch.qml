// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// On/off switch. Space or Enter toggles; announced as a checkbox with its state.
Item {
    id: sw
    property bool checked: false
    property string label: ""
    signal toggled(bool on)
    width: 36
    height: 22
    activeFocusOnTab: true

    Accessible.role: Accessible.CheckBox
    Accessible.name: label
    Accessible.checkable: true
    Accessible.checked: checked
    Accessible.onToggleAction: sw.toggled(!sw.checked)
    Accessible.onPressAction: sw.toggled(!sw.checked)

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: sw.checked ? Theme.accent : Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.20)
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 140 } }
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: -2.5
        radius: height / 2
        color: "transparent"
        border.width: 1.5
        border.color: Theme.text
        opacity: sw.activeFocus ? 0.45 : 0
    }
    Rectangle {
        id: knob
        width: 18; height: 18; radius: 9
        y: 2
        x: sw.checked ? sw.width - width - 2 : 2
        color: sw.checked ? Theme.accentText : Theme.text
        Behavior on x { enabled: !Theme.reduceMotion; NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    }
    MouseArea {
        anchors.fill: parent
        onClicked: sw.toggled(!sw.checked)
    }
    Keys.onSpacePressed: sw.toggled(!sw.checked)
    Keys.onReturnPressed: sw.toggled(!sw.checked)
    Keys.onEnterPressed: sw.toggled(!sw.checked)
}
