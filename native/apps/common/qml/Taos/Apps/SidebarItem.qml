// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// One row of a sidebar: a glyph, a title, an optional count. The current row is filled, hover and press show,
// keyboard focus draws a ring.
C.ItemDelegate {
    id: root
    property string glyph: ""
    property string count: ""
    property bool current: false
    implicitHeight: 34
    padding: 0
    topPadding: 0; bottomPadding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.RadioButton
    Accessible.name: text + (count !== "" ? ", " + count : "")
    Accessible.checked: current
    Accessible.onPressAction: clicked()
    Keys.onReturnPressed: clicked()
    background: Rectangle {
        radius: Look.radiusControl
        color: root.current ? Look.selection : root.down ? Look.pressed : root.hovered ? Look.hover : "transparent"
        border.width: root.visualFocus ? 2 : 0
        border.color: Look.focusRing
        Behavior on color { ColorAnimation { duration: Look.fast; easing.type: Look.ease } }
    }
    contentItem: RowLayout {
        spacing: Look.space3
        Glyph { Layout.leftMargin: Look.space3; name: root.glyph; size: 16; stroke: 1.8
                color: root.current ? Look.text : Look.textSecondary }
        TaosText { text: root.text; Layout.fillWidth: true; elide: Text.ElideRight
                   font.pixelSize: Look.bodyMedium; font.weight: root.current ? Font.DemiBold : Font.Normal
                   color: root.current ? Look.text : Look.textSecondary }
        TaosText { visible: root.count !== ""; text: root.count; color: Look.textTertiary; font.pixelSize: Look.small
                   font.features: { "tnum": 1 }; Layout.rightMargin: Look.space3 }
    }
}
