// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// A row or grid of large cards over the entries given: as many columns as fit (never fewer than two where the
// window allows). Arrow keys move between cards.
Item {
    id: root
    property var engine
    property var items: []
    property int maxItems: 1000
    property bool oneRow: false
    property int minCardWidth: 250
    property string tag: "card"
    signal openDetail(string id)
    readonly property int gap: Look.space4
    readonly property int columns: Math.max(1, Math.min(6, Math.floor((width + gap) / (minCardWidth + gap))))
    readonly property int shown: Math.min(items.length, oneRow ? columns : maxItems)
    implicitHeight: grid.implicitHeight
    height: implicitHeight

    function focusCell(i) { const c = rep.itemAt(i); if (c) c.forceActiveFocus(Qt.TabFocusReason) }

    StableModel { id: stable; source: root.items.slice(0, root.shown) }

    GridLayout {
        id: grid
        width: parent.width
        columns: root.columns
        columnSpacing: root.gap; rowSpacing: root.gap
        Repeater {
            id: rep
            model: stable.value
            delegate: ShelfCard {
                id: card
                required property var modelData
                required property int index
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                objectName: root.tag + "-" + modelData.id
                name: modelData.name
                category: modelData.categoryLabel ? modelData.categoryLabel : ""
                description: (modelData.description ? modelData.description : modelData.tagline) + ""
                letter: modelData.letter ? modelData.letter : ""
                iconUrl: modelData.iconUrl ? modelData.iconUrl : ""
                coverUrl: modelData.coverUrl ? modelData.coverUrl : ""
                stars: modelData.starsText ? modelData.starsText : ""
                trailing: Component {
                    GetButton { engine: root.engine; entry: card.modelData; onReview: root.openDetail(card.modelData.id) }
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
