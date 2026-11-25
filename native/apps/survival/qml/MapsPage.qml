// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Offline maps by region. Ready-made files (United States, by state) and any area of the world cut out of the newest
// Protomaps build. All map data is OpenStreetMap, ODbL; the attribution stays on the map.
C.ScrollView {
    id: page
    property bool narrow: false
    contentWidth: availableWidth
    clip: true
    readonly property var engine: Survival.mapPacks
    readonly property var packs: Survival.packs
    property string regionId: engine.regions.length > 0 ? engine.regions[0].id : ""
    property int zoom: 10
    ColumnLayout {
        width: page.availableWidth
        spacing: Look.space3
        GlassPanel {
            Layout.fillWidth: true
            implicitHeight: areaCol.implicitHeight + Look.space4 * 2
            ColumnLayout {
                id: areaCol
                anchors { fill: parent; margins: Look.space4 }
                spacing: Look.space3
                TaosText { text: qsTr("Any area"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
                           text: qsTr("Choose an area and how much detail. The map is cut out of the newest world map, reading only what that area needs. Measure first to see the size.") }
                TaosCombo {
                    Layout.fillWidth: true
                    accessibleName: qsTr("Map area")
                    model: page.engine.regions
                    textRole: "title"; valueRole: "id"
                    onActivated: { page.regionId = currentValue }
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: Look.space3
                    TaosText { text: qsTr("Detail"); font.pixelSize: Look.body; font.weight: Font.Medium }
                    C.Slider { id: zoomSlider; Layout.fillWidth: true; from: 4; to: 15; stepSize: 1; value: page.zoom; onMoved: page.zoom = value
                               Accessible.name: qsTr("Map detail, zoom level"); Accessible.role: Accessible.Slider }
                    TaosText { text: qsTr("Zoom %1").arg(page.zoom); font.pixelSize: Look.body; color: Look.textSecondary; font.features: { "tnum": 1 } }
                }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                           text: page.zoom <= 7 ? qsTr("Overview: countries and big cities.") : page.zoom <= 10 ? qsTr("Regional: towns and main roads.") : page.zoom <= 12 ? qsTr("Local: streets and villages.") : qsTr("Street level: very large for a big area.") }
                RowLayout {
                    Layout.fillWidth: true; spacing: Look.space2
                    PillButton { text: qsTr("Measure"); glyph: "search"; enabled: !page.engine.busy && page.regionId !== ""; onClicked: page.engine.estimate(page.regionId, page.zoom) }
                    PillButton { text: qsTr("Make this map"); variant: "primary"; glyph: "download"; enabled: !page.engine.busy && page.engine.estimateBytes > 0
                                 onClicked: page.engine.extract(page.regionId, page.zoom) }
                    PillButton { visible: page.engine.busy; text: qsTr("Stop"); onClicked: page.engine.cancel() }
                }
                ProgressTrack { visible: page.engine.busy; Layout.fillWidth: true; indeterminate: true }
                TaosText { visible: page.engine.status !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.engine.status; font.pixelSize: Look.body; color: Look.textSecondary }
                TaosText { visible: page.engine.error !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.engine.error; font.pixelSize: Look.body; color: Look.danger }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                           text: qsTr("Boxes are approximate and include some neighbouring land. Map data (c) OpenStreetMap contributors (ODbL), basemap by Protomaps.") }
            }
        }
        TaosText { text: qsTr("Ready-made, United States"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold; Layout.topMargin: Look.space2 }
        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                   text: qsTr("One file per state from the Project N.O.M.A.D. maps repository. The publisher posts no checksum, so only the exact size is checked, and the pack says so.") }
        Repeater {
            model: page.engine.ready
            delegate: ListRow {
                id: rrow
                required property var modelData
                readonly property var pk: { page.packs.packs; return page.packs.pack(modelData.id) }
                Layout.fillWidth: true
                clickable: false
                glyph: "map"
                title: modelData.title
                subtitle: modelData.sizeText + "  " + modelData.group
                detail: pk.error !== "" ? pk.error : pk.state === "downloading" ? qsTr("%1 of %2").arg(pk.receivedText || "0 B").arg(modelData.sizeText) : pk.state === "preparing" ? qsTr("Asking the publisher for the exact size") : ""
                trailing: Component {
                    PillButton {
                        compact: true
                        text: rrow.pk.installed ? qsTr("Installed") : rrow.pk.state === "downloading" || rrow.pk.state === "preparing" ? qsTr("Working") : qsTr("Download")
                        enabled: !rrow.pk.installed && rrow.pk.state !== "downloading" && rrow.pk.state !== "preparing"
                        variant: rrow.pk.installed ? "outline" : "tint"
                        accessibleName: qsTr("Download the map of %1").arg(rrow.modelData.title)
                        onClicked: page.engine.installReady(rrow.modelData.id)
                    }
                }
            }
        }
    }
}
