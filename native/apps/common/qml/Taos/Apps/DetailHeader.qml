// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0

// The top of an app's page: wide artwork fading into the window, the large icon, the name, one line of facts, a row
// of marks (`marks`, children) and the action (`actions`, a component).
Item {
    id: root
    property string name: ""
    property string subtitle: ""
    property string letter: ""
    property url iconUrl: ""
    property url coverUrl: ""
    property Component actions: null
    default property alias marks: markFlow.data
    implicitHeight: 152 + body.implicitHeight + Look.space3

    CoverArt {
        id: art
        width: parent.width; height: 200
        source: root.coverUrl; name: root.name
        radius: Look.radiusCard + 2
        backdrop: Look.ground
        roundBottom: true
    }
    Rectangle {   // fades the art into the page
        anchors { left: art.left; right: art.right; bottom: art.bottom }
        height: art.height * 0.9
        gradient: Gradient {
            GradientStop { position: 0.0; color: Look.veil(0.0) }
            GradientStop { position: 1.0; color: Look.veil(1.0) }
        }
    }
    RowLayout {
        id: body
        anchors { left: parent.left; right: parent.right; top: art.top; topMargin: 152 }
        anchors.leftMargin: Look.space5; anchors.rightMargin: Look.space5
        spacing: Look.space4
        AppTile { name: root.name; letter: root.letter; iconUrl: root.iconUrl; size: 96; Layout.alignment: Qt.AlignTop }
        ColumnLayout {
            Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; Layout.topMargin: Look.space4 + Look.space2
            spacing: Look.space1
            TaosText { text: root.name; Layout.fillWidth: true; elide: Text.ElideRight
                       font.pixelSize: Look.pageTitle + 4; font.weight: Font.Bold
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            TaosText { text: root.subtitle; Layout.fillWidth: true; elide: Text.ElideRight
                       font.pixelSize: Look.body; color: Look.textSecondary; font.features: { "tnum": 1 } }
            Flow { id: markFlow; Layout.fillWidth: true; Layout.topMargin: Look.space1; spacing: Look.space2 }
        }
        Loader { sourceComponent: root.actions; Layout.alignment: Qt.AlignTop; Layout.topMargin: Look.space4 + Look.space2 }
    }
}
