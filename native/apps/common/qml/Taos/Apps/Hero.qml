// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// The featured band at the top of a page: artwork, a kicker, the name, one line, and the actions (`actions`, a
// component). The art sits under a veil of the window ground so the words keep their contrast in every theme.
C.ItemDelegate {
    id: root
    property string kicker: ""
    property string name: ""
    property string tagline: ""
    property string letter: ""
    property url iconUrl: ""
    property url coverUrl: ""
    property Component actions: null
    signal open()

    implicitHeight: 280
    padding: 0
    topPadding: 0; bottomPadding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.role: Accessible.Button
    Accessible.name: name + ", " + kicker
    Accessible.description: tagline
    Accessible.onPressAction: root.open()
    onClicked: root.open()
    Keys.onReturnPressed: root.open()

    background: Rectangle {
        radius: Look.radiusCard + 2
        color: Look.surface
        border.width: root.visualFocus ? 2 : 1
        border.color: root.visualFocus ? Look.focusRing : Look.hairline
    }
    contentItem: Item {
        CoverArt {
            anchors.fill: parent; anchors.margins: 1
            source: root.coverUrl; name: root.name
            radius: Look.radiusCard + 1
            backdrop: Look.ground
            roundBottom: true
        }
        Rectangle {   // the veil: strongest where the words are
            anchors.fill: parent; anchors.margins: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Look.veil(0.96) }
                GradientStop { position: 0.45; color: Look.veil(root.hovered ? 0.62 : 0.70) }
                GradientStop { position: 1.0; color: Look.veil(0.08) }
            }
            Behavior on opacity { NumberAnimation { duration: Look.base } }
        }
        ColumnLayout {
            anchors { left: parent.left; verticalCenter: parent.verticalCenter; leftMargin: Look.space6 }
            width: Math.min(parent.width * 0.58, 560)
            spacing: Look.space2
            TaosText {
                text: root.kicker
                font.pixelSize: Look.small; font.weight: Font.DemiBold; font.letterSpacing: 1.2
                font.capitalization: Font.AllUppercase
                color: Look.textSecondary
            }
            RowLayout {
                spacing: Look.space3
                AppTile { name: root.name; letter: root.letter; iconUrl: root.iconUrl; size: 56 }
                TaosText { text: root.name; Layout.fillWidth: true; elide: Text.ElideRight
                           font.pixelSize: Look.display; font.weight: Font.Bold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            }
            TaosText {
                text: root.tagline; Layout.fillWidth: true
                wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                font.pixelSize: Look.bodyLarge; lineHeight: 1.2
                color: Look.text
            }
            Loader { sourceComponent: root.actions; Layout.topMargin: Look.space2 }
        }
    }
}
