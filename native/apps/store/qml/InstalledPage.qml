// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// Installed: everything you have, of every kind (apps, models, plugins, web apps, themes), grouped by kind.
Item {
    id: page
    StableModel { id: rowsStable; source: page.rows }
    property var engine
    property string query: ""
    signal openDetail(string id)
    signal gotoSection(string section)
    readonly property var all: visible && engine.revision >= 0 ? engine.installedRows() : []
    readonly property var rows: {
        const q = query.toLowerCase()
        const r = all.filter(x => q === "" || (x.name + " " + x.kindLabel + " " + x.subtitle).toLowerCase().indexOf(q) >= 0)
        const order = { "app": 0, "agent": 1, "webapp": 2, "model": 3, "plugin": 4, "mcp": 5, "theme": 6 }
        r.sort((a, b) => (order[a.kind] - order[b.kind]) || a.name.localeCompare(b.name))
        // a header row before each kind (no ListView sections: the rows are plain data)
        const out = []
        let last = ""
        for (const x of r) {
            if (x.kindLabel !== last) { out.push({ isHeader: true, label: x.kindLabel, id: "h-" + x.kind, source: "header" }); last = x.kindLabel }
            out.push(x)
        }
        return out
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        ColumnLayout {
            Layout.fillWidth: true; Layout.margins: 24; Layout.bottomMargin: 10; spacing: 2
            TaosText { text: qsTr("Installed"); font.pixelSize: Look.pageTitle; font.weight: Font.Bold
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            TaosText { text: qsTr("%1 items").arg(page.rows.filter(x => !x.isHeader).length); color: Look.textSecondary; font.pixelSize: Look.small
                       font.features: { "tnum": 1 } }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.bottomMargin: 20
            radius: Look.radiusCard; color: Look.surface; clip: true
            ListView {
                id: list
                objectName: "list-installed"
                anchors.fill: parent
                model: rowsStable.value
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                keyNavigationEnabled: true
                C.ScrollBar.vertical: C.ScrollBar {}
                delegate: Item {
                    id: cell
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: modelData.isHeader ? 32 : 56
                    TaosText {
                        visible: cell.modelData.isHeader === true
                        x: 14; anchors.bottom: parent.bottom; anchors.bottomMargin: 4
                        text: cell.modelData.label ? cell.modelData.label : ""
                        font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.textSecondary
                        font.capitalization: Font.AllUppercase; font.letterSpacing: 0.6
                        Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                    }
                    C.ItemDelegate {
                    id: row
                    visible: cell.modelData.isHeader !== true
                    anchors.fill: parent
                    readonly property var modelData: cell.modelData
                    padding: 0
                    hoverEnabled: true
                    objectName: modelData.isHeader ? "" : "inst-" + modelData.source + "-" + modelData.id
                    Accessible.role: Accessible.ListItem
                    Accessible.name: modelData.isHeader ? "" : modelData.name + ", " + modelData.kindLabel + ", " + modelData.subtitle
                    function go() {
                        if (modelData.isHeader) return
                        if (modelData.source === "catalog") page.openDetail(modelData.id)
                        else if (modelData.source === "webapp") page.gotoSection("webapps")
                        else page.gotoSection("themes")
                    }
                    onClicked: go()
                    Keys.onReturnPressed: go()
                    background: Rectangle {
                        color: row.down ? Look.pressed : row.hovered || row.visualFocus ? Look.hover : "transparent"
                        Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 64 } height: 1; color: Look.hairline }
                    }
                    contentItem: RowLayout {
                        spacing: 12
                        AppTile { Layout.leftMargin: 12; size: 34; letter: row.modelData.letter ? row.modelData.letter : "?"; iconUrl: row.modelData.iconUrl ? row.modelData.iconUrl : ""; name: row.modelData.name ? row.modelData.name : "" }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 1
                            TaosText { text: row.modelData.name ? row.modelData.name : ""; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                            TaosText { text: row.modelData.subtitle ? row.modelData.subtitle : ""; color: Look.textSecondary; font.pixelSize: Look.small
                                       elide: Text.ElideRight; Layout.fillWidth: true; font.features: { "tnum": 1 } }
                        }
                        Chip { visible: row.modelData.updateAvailable === true; text: qsTr("Update"); tone: "accent" }
                        Chip { visible: row.modelData.running === true; text: qsTr("Open"); tone: "success" }
                        Glyph { name: "chevron-right"; size: 14; color: Look.textTertiary; Layout.rightMargin: 12 }
                    }
                    }
                }
            }
            EmptyState {
                anchors.centerIn: parent
                visible: page.rows.length === 0
                glyph: "inbox"
                title: qsTr("Nothing installed yet")
                detail: page.query !== "" ? qsTr("No installed item matches “%1”.").arg(page.query) : qsTr("Apps, models, web apps and themes you add show up here.")
            }
            Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Look.hairline }
        }
    }
}
