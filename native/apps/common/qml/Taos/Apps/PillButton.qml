// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The rounded action of a card and a hero ("Get", "Open", "Preview"). `variant`: tint (the quiet default) |
// primary (the accent) | outline. Hover, press and keyboard focus are all visible; press eases a little.
C.Button {
    id: root
    property string variant: "tint"
    property string glyph: ""
    property string accessibleName: ""
    property bool compact: false
    implicitHeight: compact ? 28 : 34
    implicitWidth: Math.max(compact ? 56 : 72, row.implicitWidth + (compact ? 24 : 32))
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.Button
    Accessible.name: accessibleName !== "" ? accessibleName : (text !== "" ? text : glyph)
    Accessible.onPressAction: clicked()
    Keys.onReturnPressed: clicked()
    scale: down ? 0.97 : 1
    Behavior on scale { NumberAnimation { duration: Look.fast; easing.type: Look.ease } }

    readonly property bool solid: variant === "primary"
    background: Rectangle {
        radius: height / 2
        opacity: root.enabled ? 1 : 0.45
        color: root.solid ? (root.down ? Look.tint(Look.accent, 0.78) : root.hovered ? Look.tint(Look.accent, 0.90) : Look.accent)
             : root.variant === "outline" ? (root.down ? Look.pressed : root.hovered ? Look.hover : "transparent")
             : Look.tint(Look.text, root.down ? 0.20 : root.hovered ? 0.16 : 0.10)
        border.width: root.variant === "outline" ? 1 : 0
        border.color: Look.tint(Look.text, 0.28)
        Behavior on color { ColorAnimation { duration: Look.fast } }
        Rectangle {
            anchors.fill: parent; anchors.margins: -3; radius: height / 2
            color: "transparent"; border.width: 2; border.color: Look.focusRing
            visible: root.visualFocus
        }
    }
    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight
        RowLayout {
            id: row
            anchors.centerIn: parent
            spacing: 6
            Glyph { visible: root.glyph !== ""; name: root.glyph; size: root.compact ? 12 : 14; stroke: 2.2
                    color: root.solid ? Look.accentText : Look.text; Layout.alignment: Qt.AlignVCenter }
            TaosText {
                visible: root.text !== ""
                text: root.text
                font.pixelSize: root.compact ? Look.small : Look.bodyMedium
                font.weight: Font.DemiBold
                color: root.solid ? Look.accentText : Look.text
                Layout.alignment: Qt.AlignVCenter
            }
        }
    }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
}
