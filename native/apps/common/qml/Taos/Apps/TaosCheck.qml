// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// A checkbox (or, with `radio`, a radio mark) with a label. `accessibleName`
// defaults to the text.
C.CheckBox {
    id: root
    property string accessibleName: text
    property bool mono: false
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: 26
    Accessible.name: accessibleName
    Accessible.role: Accessible.CheckBox
    indicator: Rectangle {
        x: 0; y: (root.height - height) / 2
        width: 16; height: 16; radius: 5
        color: root.checked ? Look.accent : Look.field
        border.width: root.visualFocus ? 2 : 1
        border.color: root.visualFocus ? Look.focusRing : root.checked ? Look.accent : Look.textTertiary
        Behavior on color { ColorAnimation { duration: Look.fast } }
        Glyph { anchors.centerIn: parent; name: "check"; size: 11; stroke: 2.6; color: Look.accentText
                opacity: root.checked ? 1 : 0; Behavior on opacity { NumberAnimation { duration: Look.fast } } }
    }
    contentItem: TaosText {
        leftPadding: 24
        verticalAlignment: Text.AlignVCenter
        text: root.text
        font.family: root.mono ? "monospace" : Look.family
        font.pixelSize: Look.body
        color: root.enabled ? Look.text : Look.textTertiary
    }
}
