// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

C.ScrollView {
    id: page
    property bool narrow: false
    signal openRequested(string id)
    contentWidth: availableWidth
    clip: true
    readonly property var catalog: Survival.catalog
    ColumnLayout {
        width: page.availableWidth
        spacing: Look.space3
        GlassPanel {
            Layout.fillWidth: true
            implicitHeight: updRow.implicitHeight + Look.space3 * 2
            RowLayout {
                id: updRow
                anchors { fill: parent; margins: Look.space3 }
                spacing: Look.space3
                Glyph { name: "refresh"; size: 18; stroke: 1.8; color: Look.textSecondary }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
                           text: page.catalog.updateNote !== "" ? page.catalog.updateNote : qsTr("Packs from the Kiwix library can be checked for newer versions.") }
                PillButton { text: qsTr("Check for updates"); compact: true; enabled: !page.catalog.checkingUpdates; onClicked: page.catalog.checkUpdates() }
            }
        }
        PackList { Layout.fillWidth: true; kind: ""; compact: page.narrow; onOpenRequested: (id) => page.openRequested(id) }
    }
}
