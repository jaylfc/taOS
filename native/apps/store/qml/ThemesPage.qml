// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// Themes: the installed themes, with Apply. The same theme engine as Settings > Appearance: the choice is saved
// and every taOS window recolours at once. Store themes to download come later.
Item {
    id: page
    property var engine
    property string query: ""
    readonly property var all: engine.revision >= 0 ? engine.themes.themes : []
    readonly property var rows: {
        const q = query.toLowerCase()
        return all.filter(t => q === "" || (t.name + " " + t.description).toLowerCase().indexOf(q) >= 0)
    }
    C.ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: 0
            ColumnLayout {
                Layout.fillWidth: true; Layout.margins: 20; Layout.bottomMargin: 10; spacing: 2
                TaosText { text: qsTr("Themes"); font.pixelSize: Look.heading; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                TaosText {
                    Layout.fillWidth: true; wrapMode: Text.Wrap; color: Look.textSecondary; font.pixelSize: Look.small
                    text: qsTr("Pick the colours of taOS and its apps. Dark, light or automatic is chosen in Settings > Appearance.")
                }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.bottomMargin: 24
                implicitHeight: Math.max(60, col.implicitHeight)
                radius: 10; color: Look.surface; clip: true
                ColumnLayout {
                    id: col
                    width: parent.width
                    spacing: 0
                    Repeater {
                        model: page.rows
                        delegate: C.ItemDelegate {
                            id: row
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            implicitHeight: 64
                            hoverEnabled: true
                            objectName: "theme-" + modelData.id
                            Accessible.role: Accessible.ListItem
                            Accessible.name: modelData.name + ", " + modelData.origin + (modelData.current ? ", " + qsTr("in use") : "")
                            Accessible.description: modelData.description
                            onClicked: if (!modelData.current) page.engine.themes.apply(modelData.id)
                            background: Rectangle {
                                color: row.down ? Look.pressed : row.hovered || row.visualFocus ? Look.hover : "transparent"
                                Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 76 } height: 1
                                            color: Look.hairline; visible: row.index < page.rows.length - 1 }
                            }
                            contentItem: RowLayout {
                                spacing: 12
                                // the theme's own swatches: ground, surface, accent (the theme's data, not this window's tokens)
                                Rectangle {
                                    Layout.leftMargin: 12; width: 52; height: 36; radius: 7
                                    color: row.modelData.swatches[0]
                                    border.width: row.modelData.current ? 2 : 1
                                    border.color: row.modelData.current ? Look.accent : Look.hairline
                                    Rectangle { x: 6; y: 6; width: 24; height: 24; radius: 4; color: row.modelData.swatches[1] }
                                    Rectangle { x: 34; y: 9; width: 12; height: 5; radius: 2; color: row.modelData.swatches[2] }
                                    Rectangle { x: 34; y: 18; width: 8; height: 4; radius: 2; color: row.modelData.swatches[1] }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 1
                                    TaosText { text: row.modelData.name; font.weight: Font.DemiBold }
                                    TaosText { text: row.modelData.description; color: Look.textSecondary; font.pixelSize: Look.small
                                               elide: Text.ElideRight; Layout.fillWidth: true }
                                }
                                Chip { text: row.modelData.origin }
                                Chip { visible: row.modelData.current; text: qsTr("In use"); tone: "success"; glyph: "check" }
                                TaosButton {
                                    visible: !row.modelData.current
                                    text: qsTr("Apply"); variant: "primary"; compact: true; Layout.rightMargin: 12
                                    objectName: "apply-" + row.modelData.id
                                    accessibleName: qsTr("Apply the %1 theme").arg(row.modelData.name)
                                    onClicked: page.engine.themes.apply(row.modelData.id)
                                }
                                Item { visible: row.modelData.current; width: 12 }
                            }
                        }
                    }
                }
                TaosText { visible: page.rows.length === 0; anchors.centerIn: parent; color: Look.textSecondary
                           text: qsTr("No installed theme matches.") }
                Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Look.hairline }
            }
        }
    }
}
