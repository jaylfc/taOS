// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The one button of the native apps. `variant`: default | primary | success |
// danger | ghost. The accessible name defaults to the text; give `accessibleName`
// when the text alone is ambiguous (Allow, Deny). Focus shows a ring.
C.Button {
    id: root
    property string variant: "default"
    property string glyph: ""
    property string accessibleName: ""
    property bool compact: false
    implicitHeight: compact ? 26 : 32
    implicitWidth: Math.max(compact ? 30 : 36, row.implicitWidth + (compact ? 18 : 24))
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.Button
    Accessible.name: accessibleName !== "" ? accessibleName : (text !== "" ? text : glyph)
    Accessible.onPressAction: clicked()

    readonly property color tone: variant === "primary" ? Look.accent
                                : variant === "success" ? Look.success
                                : variant === "danger" ? Look.danger : Look.textSecondary
    readonly property bool solid: variant === "primary"

    background: Rectangle {
        radius: Look.radiusControl
        opacity: root.enabled ? 1 : 0.45
        color: root.solid ? (root.down ? Qt.tint(Look.accent, Look.tint(Look.ground, 0.22)) : root.hovered ? Qt.tint(Look.accent, Look.tint(Look.ground, 0.10)) : Look.accent)
             : root.variant === "ghost" ? (root.down ? Look.pressed : root.hovered ? Look.hover : "transparent")
             : Look.tint(root.tone, root.down ? 0.26 : root.hovered ? 0.20 : root.variant === "default" ? 0.0 : 0.13)
        border.width: 1
        border.color: root.solid || root.variant === "ghost" ? "transparent"
                    : root.variant === "default" ? Look.hairline : Look.tint(root.tone, 0.32)
        Rectangle {   // default variant fill (keeps hover tint layered)
            anchors.fill: parent; radius: parent.radius; visible: root.variant === "default"
            color: root.down ? Look.pressed : root.hovered ? Look.hover : Look.field
        }
        Rectangle {   // focus ring
            anchors.fill: parent; anchors.margins: -3; radius: parent.radius + 3
            color: "transparent"; border.width: 2; border.color: Look.focusRing
            visible: root.visualFocus
        }
        Behavior on color { ColorAnimation { duration: Look.fast } }
    }
    contentItem: Item {
      implicitWidth: row.implicitWidth
      implicitHeight: row.implicitHeight
      RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Glyph { visible: root.glyph !== ""; name: root.glyph; size: root.compact ? 12 : 14; stroke: 2
                color: root.solid ? Look.accentText : root.variant === "default" || root.variant === "ghost" ? Look.textSecondary : root.tone
                Layout.alignment: Qt.AlignVCenter }
        TaosText {
            visible: root.text !== ""
            text: root.text
            font.pixelSize: root.compact ? Look.small : Look.body
            font.weight: Font.Medium
            color: root.solid ? Look.accentText : root.variant === "default" || root.variant === "ghost" ? Look.text : root.tone
            Layout.alignment: Qt.AlignVCenter
        }
      }
    }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
}
