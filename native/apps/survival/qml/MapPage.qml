// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Map: the offline map and the person's markers.
StagePage {
    id: page
    pageTitle: qsTr("Map")
    property string tab: "markers"
    function focusSearch() { stage.focusSearch() }
    sidePanel: Component {
        ColumnLayout {
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: Look.space4
                spacing: Look.space3
                TaosText { text: qsTr("Map"); Layout.fillWidth: true; font.pixelSize: Look.heading; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                Segmented {
                    accessibleName: qsTr("Map panel")
                    model: [{id: "markers", text: qsTr("Markers")}, {id: "packs", text: qsTr("Packs")}]
                    currentId: page.tab
                    onActivated: (id) => page.tab = id
                }
            }
            C.ScrollView {
                visible: page.tab === "markers"
                Layout.fillWidth: true; Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    x: Look.space3
                    width: parent.width - Look.space3 * 2
                    spacing: Look.space1
                    Repeater {
                        model: page.map.markers
                        delegate: ListRow {
                            required property var modelData
                            Layout.fillWidth: true
                            glyph: "map-pin"
                            title: modelData.name
                            subtitle: modelData.coords
                            selected: page.stage.selectedMarker === modelData.id
                            onClicked: { page.stage.selectedMarker = modelData.id; page.stage.centerOn(modelData.lon, modelData.lat) }
                            trailing: Component {
                                TaosButton { glyph: "trash"; compact: true; variant: "ghost"; accessibleName: qsTr("Delete marker %1").arg(modelData.name)
                                             onClicked: { if (page.stage.selectedMarker === modelData.id) page.stage.selectedMarker = ""; page.map.removeMarker(modelData.id) } }
                            }
                        }
                    }
                    EmptyState {
                        visible: page.map.markers.length === 0
                        Layout.fillWidth: true
                        Layout.topMargin: Look.space6
                        glyph: "map-pin"
                        title: qsTr("No markers yet")
                        detail: qsTr("Tap the map and choose Drop a marker here, or press M to mark the centre.")
                    }
                    TaosText {
                        visible: page.map.stateError !== ""
                        Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.danger
                        text: page.map.stateError
                    }
                }
            }
            PackBrowser {
                visible: page.tab === "packs"
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3
                narrow: true
                tab: "maps"
                onOpenRequested: (id) => page.map.openPack(id)
            }
        }
    }
}
