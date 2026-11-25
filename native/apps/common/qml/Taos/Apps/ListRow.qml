// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// One row of a list: a glyph in a tile, a title, up to two lines under it, and a slot at the right. Hover, press,
// selection and keyboard focus all show. Without `clickable` it is a plain row.
C.ItemDelegate {
    id: root
    property string glyph: ""
    property string title: ""
    property string subtitle: ""
    property string detail: ""
    property string tone: "neutral"
    property bool selected: false
    property bool clickable: true
    property Component trailing: null
    implicitHeight: Math.max(56, textCol.implicitHeight + Look.space3 * 2)
    padding: 0
    topPadding: 0; bottomPadding: 0
    hoverEnabled: clickable
    focusPolicy: clickable ? Qt.StrongFocus : Qt.NoFocus
    Accessible.role: clickable ? Accessible.Button : Accessible.ListItem
    Accessible.name: title
    Accessible.description: subtitle
    Accessible.onPressAction: if (clickable) clicked()
    Keys.onReturnPressed: if (clickable) clicked()
    Keys.onEnterPressed: if (clickable) clicked()
    background: Rectangle {
        radius: Look.radiusCard - 4
        color: root.selected ? Look.selection : root.down && root.clickable ? Look.pressed : root.hovered && root.clickable ? Look.hover : "transparent"
        border.width: root.visualFocus ? 2 : 0
        border.color: Look.focusRing
        Behavior on color { ColorAnimation { duration: Look.fast; easing.type: Look.ease } }
    }
    contentItem: RowLayout {
        spacing: Look.space3
        Rectangle {
            visible: root.glyph !== ""
            Layout.leftMargin: Look.space3
            Layout.alignment: Qt.AlignVCenter
            width: 36; height: 36; radius: Look.radiusCard - 4
            color: root.tone === "neutral" ? Look.field : Look.tint(Look.tone(root.tone), 0.14)
            border.width: 1
            border.color: root.tone === "neutral" ? Look.hairline : Look.tint(Look.tone(root.tone), 0.30)
            Glyph { anchors.centerIn: parent; name: root.glyph; size: 18; stroke: 1.8
                    color: root.tone === "neutral" ? Look.textSecondary : Look.tone(root.tone) }
        }
        ColumnLayout {
            id: textCol
            Layout.fillWidth: true
            Layout.leftMargin: root.glyph === "" ? Look.space3 : 0
            spacing: 2
            TaosText { text: root.title; Layout.fillWidth: true; elide: Text.ElideRight
                       font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold }
            TaosText { visible: root.subtitle !== ""; text: root.subtitle; Layout.fillWidth: true; elide: Text.ElideRight
                       font.pixelSize: Look.body; color: Look.textSecondary }
            TaosText { visible: root.detail !== ""; text: root.detail; Layout.fillWidth: true; wrapMode: Text.Wrap
                       font.pixelSize: Look.small; color: Look.textTertiary }
        }
        Loader { sourceComponent: root.trailing; Layout.rightMargin: Look.space3; Layout.alignment: Qt.AlignVCenter }
    }
}
