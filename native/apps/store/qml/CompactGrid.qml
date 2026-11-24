// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// Compact rows in two columns (one when narrow): icon, name, one line, Get. Arrow keys move between rows.
Item {
    id: root
    property var engine
    property var items: []
    property int maxItems: 1000
    property string tag: "row"
    signal openDetail(string id)
    readonly property int columns: width >= 720 ? 2 : 1
    readonly property int shown: Math.min(items.length, maxItems)
    implicitHeight: grid.implicitHeight
    height: implicitHeight

    function focusCell(i) { const c = rep.itemAt(i); if (c) c.forceActiveFocus(Qt.TabFocusReason) }

    StableModel { id: stable; source: root.items.slice(0, root.shown) }

    GridLayout {
        id: grid
        width: parent.width
        columns: root.columns
        columnSpacing: Look.space5; rowSpacing: Look.space1
        Repeater {
            id: rep
            model: stable.value
            delegate: CompactRow {
                id: row
                required property var modelData
                required property int index
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                objectName: root.tag + "-" + modelData.id
                name: modelData.name
                tagline: (modelData.tagline ? modelData.tagline : modelData.description) + ""
                letter: modelData.letter ? modelData.letter : ""
                iconUrl: modelData.iconUrl ? modelData.iconUrl : ""
                stars: modelData.starsText ? modelData.starsText : ""
                trailing: Component {
                    GetButton { engine: root.engine; entry: row.modelData; onReview: root.openDetail(row.modelData.id) }
                }
                onOpen: root.openDetail(modelData.id)
                Keys.onRightPressed: root.focusCell(index + 1)
                Keys.onLeftPressed: root.focusCell(index - 1)
                Keys.onDownPressed: root.focusCell(index + root.columns)
                Keys.onUpPressed: root.focusCell(index - root.columns)
            }
        }
    }
}
