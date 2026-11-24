// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// A section of the catalogue as a list: a title, category pills and the rows. Apps, Models, Plugins, Updates.
Item {
    id: page
    property var engine
    property string section: "apps"
    property string title: ""
    property string blurb: ""
    property string query: ""
    property string chip: "all"
    signal openDetail(string id)
    readonly property var chips: engine.revision >= 0 ? engine.chips(section) : []
    readonly property var rows: engine.revision >= 0 ? engine.list(section, query, chip) : []
    onSectionChanged: chip = "all"

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: 20; Layout.bottomMargin: 10
            spacing: 6
            RowLayout {
                spacing: 8
                TaosText { text: page.title; font.pixelSize: Look.heading; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                TaosText {
                    visible: page.rows.length > 0
                    text: qsTr("%1 shown").arg(page.rows.length)
                    color: Look.textSecondary; font.pixelSize: Look.small; font.features: { "tnum": 1 }
                    Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 2
                }
            }
            TaosText {
                visible: page.blurb !== ""
                Layout.fillWidth: true; text: page.blurb; wrapMode: Text.Wrap
                color: Look.textSecondary; font.pixelSize: Look.small
            }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                visible: page.chips.length > 2
                Repeater {
                    model: page.chips
                    delegate: Pill {
                        required property var modelData
                        text: modelData.label + "  " + modelData.count
                        checked: page.chip === modelData.id
                        onClicked: page.chip = modelData.id
                    }
                }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.bottomMargin: 20
            radius: 10; color: Look.surface; clip: true
            ListView {
                id: list
                objectName: "list-" + page.section
                anchors.fill: parent
                model: page.rows
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                keyNavigationEnabled: true
                C.ScrollBar.vertical: C.ScrollBar {}
                delegate: AppRow {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    engine: page.engine
                    entry: modelData
                    last: index === page.rows.length - 1
                    objectName: "row-" + modelData.id
                    onOpen: (id) => page.openDetail(id)
                }
            }
            EmptyState {
                anchors.centerIn: parent
                visible: page.rows.length === 0
                glyph: page.engine.loading ? "clock" : "inbox"
                title: page.engine.loading ? qsTr("Loading") : page.query !== "" ? qsTr("Nothing matches") : qsTr("Nothing here")
                detail: page.query !== "" ? qsTr("No item here matches “%1”.").arg(page.query)
                        : page.engine.errorText !== "" ? page.engine.errorText : qsTr("This section is empty.")
            }
            Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Look.hairline }
        }
    }
}
