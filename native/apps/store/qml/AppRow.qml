// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// One app in a list: icon, name, a line of what it is, marks (Needs Docker, fit, installed) and its action.
// Click or Enter opens the detail page.
C.ItemDelegate {
    id: root
    property var engine
    property var entry: ({})
    property bool last: false
    signal open(string id)

    implicitHeight: 64
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.ListItem
    Accessible.name: entry.name + ", " + kindLabel + (entry.installed ? ", " + qsTr("installed") : "")
    Accessible.description: entry.tagline ? entry.tagline : entry.description
    Accessible.onPressAction: root.open(entry.id)
    readonly property string kindLabel: ({ "app": qsTr("App"), "agent": qsTr("Agent framework"), "model": qsTr("Model"),
                                           "plugin": qsTr("Plugin"), "mcp": qsTr("Tool server"), "webapp": qsTr("Web app") })[entry.kind] || ""
    onClicked: root.open(entry.id)
    Keys.onReturnPressed: root.open(entry.id)

    background: Rectangle {
        color: root.down ? Look.pressed : root.hovered || root.visualFocus ? Look.hover : "transparent"
        Behavior on color { ColorAnimation { duration: Look.fast } }
        Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 68 } height: 1
                    color: Look.hairline; visible: !root.last }
        Rectangle { anchors.fill: parent; anchors.margins: 2; radius: 6; color: "transparent"
                    border.width: 2; border.color: Look.focusRing; visible: root.visualFocus }
    }
    contentItem: RowLayout {
        spacing: 12
        AppIcon { letter: root.entry.letter ? root.entry.letter : "?"; size: 40; Layout.leftMargin: 12 }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            RowLayout {
                spacing: 8
                TaosText { text: root.entry.name; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.maximumWidth: 260 }
                Chip { visible: root.entry.needsDocker === true; text: qsTr("Needs Docker"); tone: "warning"; glyph: "alert" }
                Chip { visible: root.entry.fit === "red"; text: qsTr("Does not fit"); tone: "danger" }
                Chip { visible: root.entry.fit === "yellow"; text: qsTr("May be slow"); tone: "warning" }
                Chip { visible: root.entry.updateAvailable === true; text: qsTr("Update"); tone: "accent" }
            }
            TaosText {
                Layout.fillWidth: true
                text: (root.entry.tagline ? root.entry.tagline : root.entry.description) + ""
                elide: Text.ElideRight
                color: Look.textSecondary
                font.pixelSize: Look.small
            }
        }
        TaosText {
            visible: root.width > 640
            text: root.entry.kind === "model" && root.entry.sizeText ? root.entry.sizeText : root.kindLabel
            color: Look.textTertiary
            font.pixelSize: Look.small
            font.features: { "tnum": 1 }
        }
        StateButton { engine: root.engine; entry: root.entry; Layout.rightMargin: 12; onReview: root.open(root.entry.id) }
    }
}
