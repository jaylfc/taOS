// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// AI models. taOS already has a model store, so nothing is downloaded here: this lists what Project N.O.M.A.D. offers
// and opens the Store at its models section.
C.ScrollView {
    id: page
    property bool narrow: false
    contentWidth: availableWidth
    clip: true
    readonly property var engine: Survival.models
    ColumnLayout {
        width: page.availableWidth
        spacing: Look.space3
        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
                   text: qsTr("Models are installed from the taOS Store, which knows what your hardware can run. The list below is what Project N.O.M.A.D. offers, to look for in the Store.") }
        PillButton { text: qsTr("Open the Store at Models"); variant: "primary"; glyph: "external"; onClicked: page.engine.openStore() }
        Repeater {
            model: page.engine.models
            delegate: ListRow {
                id: mrow
                required property var modelData
                Layout.fillWidth: true
                clickable: false
                glyph: "cpu"
                title: modelData.name
                subtitle: modelData.description
                detail: modelData.tags.map(t => t.tag + " (" + t.size + ")").join("   ")
            }
        }
        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.caption; color: Look.textTertiary; text: page.engine.note }
    }
}
