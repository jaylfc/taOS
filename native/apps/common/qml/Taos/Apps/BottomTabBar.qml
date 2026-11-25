// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The phone's section bar: up to five tabs with a glyph and a short label, big enough for a thumb. `model` is a list
// of {id, text, glyph}. Left and Right move between tabs.
FocusScope {
    id: root
    property var model: []
    property string currentId: ""
    signal activated(string id)
    implicitHeight: 64
    activeFocusOnTab: true
    Accessible.role: Accessible.PageTabList
    Accessible.name: qsTr("Sections")
    function step(d) {
        const n = model.length
        let i = 0
        for (let k = 0; k < n; k++) if (model[k].id === currentId) i = k
        const next = model[(i + d + n) % n].id
        root.currentId = next
        root.activated(next)
    }
    Keys.onLeftPressed: step(-1)
    Keys.onRightPressed: step(1)
    Rectangle { anchors.fill: parent; color: Look.ground }
    Rectangle { anchors { left: parent.left; right: parent.right; top: parent.top } height: 1; color: Look.hairline }
    Rectangle {
        anchors { fill: parent; margins: 2 }
        radius: Look.radiusControl
        color: "transparent"
        border.width: root.activeFocus ? 2 : 0
        border.color: Look.focusRing
    }
    RowLayout {
        anchors.fill: parent
        spacing: 0
        Repeater {
            model: root.model
            delegate: C.AbstractButton {
                id: tab
                required property var modelData
                readonly property bool on: root.currentId === modelData.id
                Layout.fillWidth: true
                Layout.fillHeight: true
                focusPolicy: Qt.NoFocus
                hoverEnabled: true
                Accessible.role: Accessible.PageTab
                Accessible.name: modelData.text
                Accessible.checked: on
                Accessible.onPressAction: clicked()
                onClicked: { root.currentId = modelData.id; root.activated(modelData.id) }
                background: Item {}
                contentItem: ColumnLayout {
                    spacing: 3
                    Item { Layout.fillHeight: true }
                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        width: 52; height: 28; radius: 14
                        color: tab.on ? Look.selection : tab.down ? Look.pressed : "transparent"
                        Behavior on color { ColorAnimation { duration: Look.fast } }
                        Glyph { anchors.centerIn: parent; name: tab.modelData.glyph; size: 20; stroke: 1.8
                                color: tab.on ? Look.text : Look.textSecondary }
                    }
                    TaosText {
                        Layout.alignment: Qt.AlignHCenter
                        text: tab.modelData.text
                        font.pixelSize: Look.caption
                        font.weight: tab.on ? Font.DemiBold : Font.Medium
                        color: tab.on ? Look.text : Look.textSecondary
                    }
                    Item { Layout.fillHeight: true }
                }
            }
        }
    }
}
