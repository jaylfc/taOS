import QtQuick
import Taos.Bar

// The mute toggle at the start of a slider, Mac style: the speaker (or
// microphone) glyph itself is the button. Announced as a toggle with its state.
Item {
    id: mb
    property bool muted: false
    property string label: ""
    property string glyph: "speaker"   // speaker or mic
    property string level: "high"      // speaker only
    signal toggled(bool muted)
    width: 28
    height: 28
    activeFocusOnTab: true

    Accessible.role: Accessible.CheckBox
    Accessible.name: label
    Accessible.checkable: true
    Accessible.checked: muted
    Accessible.onPressAction: mb.toggled(!mb.muted)
    Accessible.onToggleAction: mb.toggled(!mb.muted)

    Rectangle {
        anchors.fill: parent
        radius: 8
        color: tap.pressed ? Theme.pressed : (hover.hovered || mb.activeFocus) ? Theme.hover : "transparent"
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 100 } }
    }
    Rectangle { anchors.fill: parent; radius: 8; color: "transparent"; border.width: 1.5; border.color: Theme.text; opacity: mb.activeFocus ? 0.4 : 0 }
    HoverHandler { id: hover }
    VolumeIcon { visible: mb.glyph === "speaker"; level: mb.muted ? "muted" : mb.level; anchors.centerIn: parent }
    DeviceGlyph { visible: mb.glyph === "mic"; kind: "mic"; anchors.centerIn: parent; color: mb.muted ? Theme.textTertiary : Theme.text }
    Rectangle {
        visible: mb.glyph === "mic" && mb.muted
        anchors.centerIn: parent; width: 22; height: 1.5; radius: 1; rotation: -40; color: Theme.danger
    }
    MouseArea { id: tap; anchors.fill: parent; onClicked: mb.toggled(!mb.muted) }
    Keys.onSpacePressed: mb.toggled(!mb.muted)
    Keys.onReturnPressed: mb.toggled(!mb.muted)
}
