// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// The agent panel every Studio carries. There is no shared agent chat component yet, so this is a clearly labelled
// stand-in: it shows what the agent will do here, and it never shows a made-up reply. When the shared chat
// component exists it replaces the body of this panel and the mode switch and pop-out stay as they are.
ColumnLayout {
    id: root
    property string studio: ""
    property var examples: []
    property bool poppedOut: false
    signal popOutRequested()
    spacing: 0
    Accessible.role: Accessible.Pane
    Accessible.name: qsTr("Agent")

    RowLayout {
        Layout.fillWidth: true
        Layout.margins: Look.space4
        spacing: Look.space2
        Glyph { name: "sparkles"; size: 18; stroke: 1.8; color: Look.textSecondary }
        TaosText { text: qsTr("Agent"); Layout.fillWidth: true; font.pixelSize: Look.title; font.weight: Font.DemiBold
                   Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
        TaosButton {
            glyph: root.poppedOut ? "panel-right" : "external"
            compact: true
            variant: "ghost"
            accessibleName: root.poppedOut ? qsTr("Dock the agent panel") : qsTr("Pop the agent out into its own window")
            onClicked: root.popOutRequested()
        }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
    ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.margins: Look.space4
        spacing: Look.space3
        Chip { text: qsTr("Not connected yet"); tone: "warning"; glyph: "info" }
        TaosText {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font.pixelSize: Look.body
            color: Look.textSecondary
            text: qsTr("The agent is not wired into %1 yet, so there is nothing to ask. This panel is where it will live, and it will not show any reply it did not really give.").arg(root.studio)
        }
        TaosText { text: qsTr("What it will do here"); font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textTertiary
                   font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; Layout.topMargin: Look.space2 }
        Repeater {
            model: root.examples
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: Look.space2
                Glyph { name: "check"; size: 14; stroke: 2; color: Look.textTertiary; Layout.alignment: Qt.AlignTop; Layout.topMargin: 2 }
                TaosText { text: modelData; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
            }
        }
        TaosText { text: qsTr("Two ways to work"); font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textTertiary
                   font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; Layout.topMargin: Look.space2 }
        Segmented {
            Layout.fillWidth: true
            enabled: false
            opacity: 0.55
            accessibleName: qsTr("Agent mode, not available yet")
            model: [{id: "propose", text: qsTr("Propose")}, {id: "coedit", text: qsTr("Co-edit")}]
            currentId: "propose"
        }
        TaosText {
            Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
            text: qsTr("Propose: the agent suggests and you approve each change. Co-edit: it works beside you live.")
        }
        Item { Layout.fillHeight: true }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
    TaosField {
        Layout.fillWidth: true
        Layout.margins: Look.space3
        enabled: false
        placeholderText: qsTr("Ask the agent (not available yet)")
        accessibleName: qsTr("Message to the agent, not available yet")
    }
}
