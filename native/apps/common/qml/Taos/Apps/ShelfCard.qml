// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// A large card for a shelf: banner art with a category chip, the icon, name and category, three lines of
// description, a star count and an action (`trailing`, a component: the Store puts its Get button there).
// Click or Enter opens; the card never knows what the action does.
C.ItemDelegate {
    id: root
    property string name: ""
    property string category: ""
    property string description: ""
    property string letter: ""
    property url iconUrl: ""
    property url coverUrl: ""
    property string stars: ""
    property Component trailing: null
    signal open()

    implicitHeight: 292
    padding: 0
    topPadding: 0; bottomPadding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.Button
    Accessible.name: name + ", " + category
    Accessible.description: description
    Accessible.onPressAction: root.open()
    onClicked: root.open()
    Keys.onReturnPressed: root.open()

    readonly property color fill: down ? Look.raised : hovered || visualFocus ? Look.raised : Look.surface
    background: Rectangle {
        radius: Look.radiusCard
        color: root.fill
        border.width: root.visualFocus ? 2 : 1
        border.color: root.visualFocus ? Look.focusRing : Look.hairline
        Behavior on color { ColorAnimation { duration: Look.fast; easing.type: Look.ease } }
    }
    contentItem: ColumnLayout {
        spacing: 0
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(root.width * 0.40)
            Layout.margins: 1
            CoverArt {
                anchors.fill: parent
                source: root.coverUrl
                name: root.name
                radius: Look.radiusCard - 1
                backdrop: root.fill
            }
            AppTile {   // no banner art for this app: its icon, large, on the quiet cover
                visible: root.coverUrl.toString() === ""
                anchors.centerIn: parent
                name: root.name; letter: root.letter; iconUrl: root.iconUrl
                size: 64
                opacity: 0.9
            }
            Rectangle {
                visible: root.category !== ""
                x: Look.space3; y: Look.space3
                width: chipLabel.implicitWidth + 16; height: 22; radius: 11
                color: Look.veil(0.78)
                border.width: 1; border.color: Look.hairline
                TaosText {
                    id: chipLabel
                    anchors.centerIn: parent
                    text: root.category
                    font.pixelSize: Look.caption; font.weight: Font.DemiBold; font.letterSpacing: 0.6
                    font.capitalization: Font.AllUppercase
                    color: Look.text
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.leftMargin: Look.space4; Layout.rightMargin: Look.space4; Layout.topMargin: Look.space3; Layout.bottomMargin: Look.space3
            spacing: Look.space2
            RowLayout {
                spacing: Look.space3
                AppTile { name: root.name; letter: root.letter; iconUrl: root.iconUrl; size: 40 }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    TaosText { text: root.name; Layout.fillWidth: true; elide: Text.ElideRight
                               font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                    TaosText { text: root.category; Layout.fillWidth: true; elide: Text.ElideRight
                               font.pixelSize: Look.small; color: Look.textSecondary }
                }
            }
            TaosText {
                Layout.fillWidth: true; Layout.fillHeight: true
                text: root.description
                wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; lineHeight: 1.2
                font.pixelSize: Look.body; color: Look.textSecondary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Look.space1
                Glyph { visible: root.stars !== ""; name: "star"; size: 13; stroke: 2; color: Look.textSecondary }
                TaosText { visible: root.stars !== ""; text: root.stars; font.pixelSize: Look.small; color: Look.textSecondary
                           font.features: { "tnum": 1 } }
                Item { Layout.fillWidth: true }
                Loader { sourceComponent: root.trailing; Layout.alignment: Qt.AlignVCenter }
            }
        }
    }
}
