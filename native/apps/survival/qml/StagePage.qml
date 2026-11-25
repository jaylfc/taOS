// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// A page that is the map with a panel beside it (wide) or a sheet under it (phone). Without a map pack it says so and
// offers the map packs instead.
Item {
    id: sp
    objectName: "page-" + (takMode ? "taostak" : "map")
    property bool narrow: false
    property bool takMode: false
    property string pageTitle: ""
    property string pageSubtitle: ""
    property Component sidePanel
    property alias stage: stageItem
    property bool sheetOpen: true
    readonly property var map: Survival.map
    function focusSearch() { stageItem.focusSearch() }

    Component.onCompleted: map.openFirstAvailable()
    Connections { target: Survival.packs; function onPackInstalled(id) { if (!sp.map.loaded) sp.map.openFirstAvailable() } }

    // no map yet
    C.ScrollView {
        visible: !sp.map.loaded
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            x: sp.narrow ? Look.space4 : Look.space5
            y: sp.narrow ? Look.space4 : Look.space5
            width: Math.min(820, parent.width - 2 * x)
            spacing: Look.space4
            TaosText { text: sp.pageTitle; font.pixelSize: Look.pageTitle; font.weight: Font.DemiBold
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            EmptyState {
                Layout.fillWidth: true
                Layout.topMargin: Look.space4
                glyph: "map"
                title: sp.map.error !== "" ? qsTr("This map could not be opened") : qsTr("No map yet")
                detail: sp.map.error !== "" ? sp.map.error : qsTr("Import or download a map pack and the map works offline from then on.")
                tone: sp.map.error !== "" ? "error" : "neutral"
            }
            PackList { Layout.fillWidth: true; kind: "map"; compact: sp.narrow; onOpenRequested: (id) => sp.map.openPack(id) }
        }
    }

    MapStage {
        id: stageItem
        visible: sp.map.loaded
        takMode: sp.takMode
        narrow: sp.narrow
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.right: sp.narrow ? parent.right : panel.left
        anchors.bottom: sp.narrow ? panel.top : parent.bottom
    }

    Rectangle {
        id: panel
        visible: sp.map.loaded
        color: Look.ground
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.top: sp.narrow ? undefined : parent.top
        anchors.left: sp.narrow ? parent.left : undefined
        width: sp.narrow ? parent.width : 340
        height: sp.narrow ? (sp.sheetOpen ? Math.round(sp.height * 0.48) : 52) : sp.height
        Behavior on height { NumberAnimation { duration: Look.base; easing.type: Look.ease } }
        Rectangle { width: sp.narrow ? parent.width : 1; height: sp.narrow ? 1 : parent.height; color: Look.hairline }
        // the handle of the phone sheet
        C.AbstractButton {
            id: handle
            visible: sp.narrow
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 52
            focusPolicy: Qt.StrongFocus
            Accessible.role: Accessible.Button
            Accessible.name: sp.sheetOpen ? qsTr("Close the panel") : qsTr("Open the panel")
            Accessible.onPressAction: clicked()
            onClicked: sp.sheetOpen = !sp.sheetOpen
            Keys.onReturnPressed: clicked()
            background: Rectangle { color: "transparent"; border.width: handle.visualFocus ? 2 : 0; border.color: Look.focusRing }
            contentItem: Item {
                Rectangle { anchors.horizontalCenter: parent.horizontalCenter; y: 8; width: 36; height: 4; radius: 2; color: Look.tint(Look.text, 0.25) }
                TaosText { anchors { left: parent.left; leftMargin: Look.space4; verticalCenter: parent.verticalCenter; verticalCenterOffset: 4 }
                           text: sp.sheetOpen ? qsTr("Hide details") : qsTr("Show details"); font.pixelSize: Look.small; color: Look.textSecondary }
                Glyph { anchors { right: parent.right; rightMargin: Look.space4; verticalCenter: parent.verticalCenter; verticalCenterOffset: 4 }
                        name: sp.sheetOpen ? "chevron-down" : "chevron-right"; rotation: sp.sheetOpen ? 0 : -90; size: 18; stroke: 2; color: Look.textSecondary }
            }
        }
        Loader {
            id: panelLoader
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; top: parent.top; topMargin: sp.narrow ? 52 : 0 }
            clip: true
            sourceComponent: sp.sidePanel
            visible: !sp.narrow || sp.sheetOpen
        }
    }
}
