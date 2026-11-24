// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// "Appearance": Dark, Light or Auto, and the theme swatches. A click, or Enter or
// Space on the focused choice, applies it live and saves it; the choice is spoken
// ("Theme: Graphite + Ice, dark"). Left and Right move within a group.
Column {
    id: sec
    width: parent ? parent.width : 320
    spacing: 2
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"

    Rectangle { width: parent.width; height: 1; color: Theme.stroke; opacity: 0.8 }
    Text {
        leftPadding: 10; topPadding: 8; bottomPadding: 2
        text: qsTr("Appearance")
        color: Theme.text
        font.family: sec.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
        Accessible.role: Accessible.Heading
        Accessible.name: text
    }
    Row {
        id: modeRow
        x: 10
        spacing: 6
        Repeater {
            model: Themes.modes
            delegate: ChoiceChip {
                required property var modelData
                text: modelData.label
                checked: modelData.current
                accessibleName: checked ? qsTr("%1, selected").arg(modelData.label) : modelData.label
                onActivated: Themes.setMode(modelData.id)
            }
        }
    }
    Text {
        visible: Themes.mode === "auto"
        width: parent.width
        leftPadding: 10; rightPadding: 10; topPadding: 2
        wrapMode: Text.WordWrap
        text: qsTr("Light from 07:00 to 19:00, dark otherwise.")
        color: Theme.textTertiary
        font.family: sec.fontFamily; font.pixelSize: 11
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }
    Item { width: 1; height: 4 }
    Flow {
        id: chipFlow
        x: 10
        width: parent.width - 20
        spacing: 6
        Repeater {
            model: Themes.themes
            delegate: SwatchChip {
                required property var modelData
                width: Math.floor((chipFlow.width - 2 * chipFlow.spacing) / 3)
                name: modelData.name
                description: modelData.description
                swatches: modelData.swatches
                checked: modelData.current
                onActivated: Themes.setTheme(modelData.id)
            }
        }
    }
    Text {
        leftPadding: 10; topPadding: 8; bottomPadding: 2
        text: qsTr("Window buttons")
        color: Theme.text
        font.family: sec.fontFamily; font.pixelSize: 13; font.weight: Font.Bold
        Accessible.role: Accessible.Heading
        Accessible.name: text
    }
    Row {
        x: 10
        spacing: 6
        Repeater {
            model: [{ id: "left", label: qsTr("Left") }, { id: "right", label: qsTr("Right") }, { id: "theme", label: qsTr("Theme") }]
            delegate: ChoiceChip {
                required property var modelData
                text: modelData.label
                checked: Themes.windowButtonsChoice === modelData.id
                accessibleName: checked ? qsTr("%1, selected").arg(modelData.label) : qsTr("Window buttons %1").arg(modelData.label)
                onActivated: Themes.setWindowButtons(modelData.id)
            }
        }
    }
    Item { width: 1; height: 4 }
    // Spoken, not shown.
    Item { id: live; width: 1; height: 1; Accessible.role: Accessible.StaticText; Accessible.name: "" }
    Connections { target: Themes; function onAnnounce(t) { MenuHost.announce(live, t) } }
}
