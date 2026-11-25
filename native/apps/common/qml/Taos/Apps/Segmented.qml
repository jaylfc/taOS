// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// A segmented control: a few exclusive choices in one pill. `model` is a list of {id, text, glyph?}; the current
// choice is `currentId`. Left and Right move between choices, Space and Return choose.
FocusScope {
    id: root
    property var model: []
    property string currentId: ""
    property string accessibleName: ""
    signal activated(string id)
    implicitHeight: 32
    implicitWidth: row.implicitWidth + 4
    activeFocusOnTab: true
    Accessible.role: Accessible.RadioButton
    Accessible.name: accessibleName
    function step(d) {
        const n = model.length
        if (n === 0) return
        let i = 0
        for (let k = 0; k < n; k++) if (model[k].id === currentId) i = k
        const next = model[(i + d + n) % n].id
        root.currentId = next
        root.activated(next)
    }
    Keys.onLeftPressed: step(-1)
    Keys.onRightPressed: step(1)
    Rectangle {
        anchors.fill: parent
        radius: Look.radiusControl
        color: Look.field
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Look.focusRing : Look.hairline
    }
    RowLayout {
        id: row
        anchors { fill: parent; margins: 2 }
        spacing: 2
        Repeater {
            model: root.model
            delegate: C.AbstractButton {
                id: seg
                required property var modelData
                readonly property bool on: root.currentId === modelData.id
                Layout.fillWidth: true
                Layout.fillHeight: true
                implicitWidth: lbl.implicitWidth + (modelData.glyph ? 34 : 24)
                hoverEnabled: true
                focusPolicy: Qt.NoFocus
                Accessible.role: Accessible.RadioButton
                Accessible.name: modelData.text
                Accessible.checked: on
                Accessible.onPressAction: clicked()
                onClicked: { root.currentId = modelData.id; root.activated(modelData.id); root.forceActiveFocus() }
                background: Rectangle {
                    radius: Look.radiusControl - 2
                    color: seg.on ? Look.selection : seg.down ? Look.pressed : seg.hovered ? Look.hover : "transparent"
                    Behavior on color { ColorAnimation { duration: Look.fast; easing.type: Look.ease } }
                }
                contentItem: RowLayout {
                    spacing: 6
                    Item { Layout.fillWidth: true }
                    Glyph { visible: !!seg.modelData.glyph; name: seg.modelData.glyph || ""; size: 14; stroke: 1.9
                            color: seg.on ? Look.text : Look.textSecondary }
                    TaosText {
                        id: lbl
                        text: seg.modelData.text
                        font.pixelSize: Look.body
                        font.weight: seg.on ? Font.DemiBold : Font.Medium
                        color: seg.on ? Look.text : Look.textSecondary
                    }
                    Item { Layout.fillWidth: true }
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }
    }
}
