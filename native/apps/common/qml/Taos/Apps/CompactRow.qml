// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// A compact row for a two-column shelf: icon, name, one line of what it is with its star count, and an action.
C.ItemDelegate {
    id: root
    property string name: ""
    property string tagline: ""
    property string letter: ""
    property url iconUrl: ""
    property string stars: ""
    property Component trailing: null
    signal open()

    implicitHeight: 64
    padding: 0
    topPadding: 0; bottomPadding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.Button
    Accessible.name: name
    Accessible.description: tagline
    Accessible.onPressAction: root.open()
    onClicked: root.open()
    Keys.onReturnPressed: root.open()

    background: Rectangle {
        radius: Look.radiusCard - 2
        color: root.down ? Look.pressed : root.hovered ? Look.hover : "transparent"
        border.width: root.visualFocus ? 2 : 0
        border.color: Look.focusRing
        Behavior on color { ColorAnimation { duration: Look.fast; easing.type: Look.ease } }
    }
    contentItem: RowLayout {
        spacing: Look.space3
        AppTile { Layout.leftMargin: Look.space3; name: root.name; letter: root.letter; iconUrl: root.iconUrl; size: 46 }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            TaosText { text: root.name; Layout.fillWidth: true; elide: Text.ElideRight
                       font.pixelSize: Look.title; font.weight: Font.DemiBold }
            RowLayout {
                spacing: Look.space1
                Layout.fillWidth: true
                TaosText { text: root.tagline; Layout.fillWidth: true; elide: Text.ElideRight
                           font.pixelSize: Look.body; color: Look.textSecondary }
                Glyph { visible: root.stars !== ""; name: "star"; size: 12; stroke: 2; color: Look.textSecondary }
                TaosText { visible: root.stars !== ""; text: root.stars; font.pixelSize: Look.small; color: Look.textSecondary
                           font.features: { "tnum": 1 } }
            }
        }
        Loader { sourceComponent: root.trailing; Layout.rightMargin: Look.space3; Layout.alignment: Qt.AlignVCenter }
    }
}
