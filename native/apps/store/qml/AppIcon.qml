// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// The icon of an app: the picture when there is one, else its first letter on a neutral tile. Decorative: the
// row or page that holds it carries the name.
Item {
    id: root
    property string letter: "?"
    property url iconUrl: ""
    property int size: 40
    implicitWidth: size
    implicitHeight: size
    Accessible.ignored: true

    Rectangle {
        anchors.fill: parent
        radius: root.size * 0.24
        color: Look.field
        border.width: 1
        border.color: Look.hairline
    }
    Image {
        id: img
        anchors.fill: parent
        anchors.margins: root.size * 0.12
        source: root.iconUrl
        sourceSize: Qt.size(root.size * 2, root.size * 2)
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        visible: status === Image.Ready
    }
    TaosText {
        anchors.centerIn: parent
        visible: img.status !== Image.Ready
        text: root.letter
        font.pixelSize: root.size * 0.42
        font.weight: Font.DemiBold
        color: Look.textSecondary
    }
}
