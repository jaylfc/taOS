// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

// Search as you type across every setting. Enter opens the first (or the chosen) result.
FocusScope {
    id: res
    property string query: ""
    readonly property var results: Settings.catalog.search(query)
    function openCurrent() {
        if (list.currentIndex < 0 || list.currentIndex >= results.length) return
        const r = results[list.currentIndex]
        Settings.nav.navigate(r.section, r.kind === "setting" ? r.id : "")
    }
    onResultsChanged: { list.currentIndex = results.length ? 0 : -1 }

    Txt {
        anchors.centerIn: parent
        visible: res.results.length === 0
        width: Math.min(parent.width - 48, 360)
        horizontalAlignment: Text.AlignHCenter
        text: qsTr("Nothing matches \"%1\".").arg(res.query)
        color: TaosStyle.textSecondary
        Accessible.role: Accessible.StaticText
    }
    ListView {
        id: list
        anchors { fill: parent; margins: 16 }
        visible: res.results.length > 0
        model: res.results
        clip: true
        focus: true
        keyNavigationEnabled: true
        activeFocusOnTab: true
        spacing: 2
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Search results")
        Keys.onReturnPressed: res.openCurrent()
        Keys.onEnterPressed: res.openCurrent()
        delegate: Item {
            id: d
            required property var modelData
            required property int index
            width: ListView.view.width
            height: 54
            Accessible.role: Accessible.ListItem
            Accessible.name: modelData.title
            Accessible.description: qsTr("in %1").arg(modelData.sectionTitle)
            Accessible.selected: ListView.isCurrentItem
            Rectangle {
                anchors.fill: parent; radius: 8
                color: d.ListView.isCurrentItem ? TaosStyle.selection : (hover.hovered ? TaosStyle.raised : "transparent")
                border.width: d.ListView.isCurrentItem && list.activeFocus ? 2 : 0
                border.color: TaosStyle.focusRing
            }
            ColumnLayout {
                anchors { fill: parent; leftMargin: 14; rightMargin: 14 }
                spacing: 1
                Txt { Layout.fillWidth: true; text: d.modelData.title; wrapMode: Text.NoWrap; elide: Text.ElideRight; Accessible.ignored: true }
                Txt {
                    Layout.fillWidth: true; wrapMode: Text.NoWrap; elide: Text.ElideRight
                    text: d.modelData.kind === "setting" ? d.modelData.sectionTitle : qsTr("Section")
                    font.pixelSize: 12; color: TaosStyle.textSecondary; Accessible.ignored: true
                }
            }
            HoverHandler { id: hover }
            TapHandler { onTapped: { list.currentIndex = d.index; res.openCurrent() } }
        }
    }
}
