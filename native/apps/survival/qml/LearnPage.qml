// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Education channels (Khan Academy and others) from the Kolibri catalogue. They are imported into the Kolibri that
// runs in the survival-studio container; Kolibri downloads and checks the content itself.
ColumnLayout {
    id: page
    property bool narrow: false
    readonly property var engine: Survival.edu
    property bool accepted: false
    spacing: Look.space3
    Component.onCompleted: engine.load()
    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
               text: qsTr("Courses and lessons for school ages and up, taught with videos and exercises. They are imported into Kolibri, which runs in the survival-studio container.") }
    TaosField { id: filter; Layout.fillWidth: true; leftPadding: 34; placeholderText: qsTr("Filter channels"); accessibleName: qsTr("Filter education channels")
                Glyph { x: 11; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; stroke: 2; color: Look.textTertiary } }
    TaosCheck { text: qsTr("I accept the licence terms of channels I install (most restrict commercial use; each channel states its own)"); checked: page.accepted; onToggled: page.accepted = checked }
    TaosText { visible: page.engine.message !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.engine.message; font.pixelSize: Look.body; color: Look.textSecondary }
    C.ScrollView {
        Layout.fillWidth: true; Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: Look.space2
            Repeater { model: page.engine.loading ? 4 : 0; delegate: Skeleton { Layout.fillWidth: true; Layout.preferredHeight: 80; radius: Look.radiusCard } }
            EmptyState { visible: page.engine.error !== ""; Layout.fillWidth: true; Layout.topMargin: Look.space5; glyph: "alert"; tone: "error"
                         title: qsTr("The education catalogue could not be reached"); detail: page.engine.error }
            PillButton { visible: page.engine.error !== ""; Layout.alignment: Qt.AlignHCenter; text: qsTr("Try again"); onClicked: page.engine.load() }
            Repeater {
                model: page.engine.channels.filter(c => filter.text === "" || (c.name + " " + c.description).toLowerCase().indexOf(filter.text.toLowerCase()) >= 0)
                delegate: GlassPanel {
                    id: ch
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: chCol.implicitHeight + Look.space3 * 2
                    ColumnLayout {
                        id: chCol
                        anchors { fill: parent; margins: Look.space3 }
                        spacing: Look.space2
                        RowLayout {
                            Layout.fillWidth: true; spacing: Look.space3
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                TaosText { text: ch.modelData.name; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.title; font.weight: Font.DemiBold }
                                TaosText { text: ch.modelData.description; Layout.fillWidth: true; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.pixelSize: Look.body; color: Look.textSecondary }
                            }
                            PillButton {
                                visible: !ch.modelData.installed
                                variant: "primary"; glyph: "download"; text: ch.modelData.importing ? qsTr("Importing...") : qsTr("Install")
                                enabled: page.accepted && page.engine.importing === ""
                                accessibleName: qsTr("Install the channel %1 in Kolibri").arg(ch.modelData.name)
                                onClicked: page.engine.install(ch.modelData.id, page.accepted)
                            }
                        }
                        Flow {
                            Layout.fillWidth: true; spacing: Look.space2
                            Chip { text: ch.modelData.sizeText; glyph: "download" }
                            Chip { text: qsTr("%L1 lessons").arg(ch.modelData.resources) }
                            Chip { visible: ch.modelData.language !== ""; text: ch.modelData.language.toUpperCase() }
                            Chip { visible: ch.modelData.lastPublished !== ""; text: ch.modelData.lastPublished; mono: true }
                            Chip { visible: ch.modelData.installed; text: qsTr("Installed"); tone: "success"; glyph: "check" }
                        }
                        TaosText { text: qsTr("From Learning Equality (Kolibri Studio)"); font.pixelSize: Look.caption; color: Look.textTertiary }
                    }
                }
            }
        }
    }
}
