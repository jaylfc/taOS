// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// Discover: a few rows of what is worth a look, grouped. Web apps first (YouTube and the others you can add in a
// click), then popular apps, models that run well on this device, and plugins.
Item {
    id: page
    property var engine
    signal openDetail(string id)
    signal gotoSection(string section)
    readonly property var groups: engine.revision >= 0 ? engine.discover() : []

    C.ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: 0
            ColumnLayout {
                Layout.fillWidth: true; Layout.margins: 20; Layout.bottomMargin: 4; spacing: 2
                TaosText { text: qsTr("Discover"); font.pixelSize: Look.heading; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                TaosText { text: qsTr("Apps, web apps, models and themes for this device."); color: Look.textSecondary; font.pixelSize: Look.small }
            }
            Repeater {
                model: page.groups
                delegate: ColumnLayout {
                    id: g
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 0
                    RowLayout {
                        Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 20; Layout.topMargin: 18; Layout.bottomMargin: 6
                        TaosText { text: g.modelData.title; Layout.fillWidth: true; font.pixelSize: Look.small; font.weight: Font.DemiBold
                                   color: Look.textSecondary; font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                                   Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                        TaosButton {
                            visible: g.modelData.total > g.modelData.items.length
                            text: qsTr("See all %1").arg(g.modelData.total); variant: "ghost"; compact: true
                            onClicked: page.gotoSection(g.modelData.id)
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true; Layout.leftMargin: 20; Layout.rightMargin: 20
                        implicitHeight: rows.implicitHeight
                        radius: 10; color: Look.surface; clip: true
                        ColumnLayout {
                            id: rows
                            width: parent.width
                            spacing: 0
                            Repeater {
                                model: g.modelData.items
                                delegate: AppRow {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    engine: page.engine
                                    entry: modelData
                                    last: index === g.modelData.items.length - 1
                                    objectName: "disc-" + modelData.id
                                    onOpen: (id) => page.openDetail(id)
                                }
                            }
                        }
                        Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Look.hairline }
                    }
                }
            }
            Item { Layout.preferredHeight: 24 }
        }
    }
}
