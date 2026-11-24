// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// The icon of an app. Order: its picture (a full-bleed SVG tile as it is, a PNG logo on a plate), else a letter
// tile whose fill is picked from the name inside the theme's neutral range (Look.tileFill). Decorative: the row or
// page that holds it carries the name.
Item {
    id: root
    property string name: ""
    property string letter: ""
    property url iconUrl: ""
    property real size: 40
    readonly property bool bleed: String(iconUrl).toLowerCase().indexOf(".svg") > 0
    readonly property bool hasIcon: img.status === Image.Ready
    readonly property string mark: letter !== "" ? letter : (name !== "" ? name.charAt(0).toUpperCase() : "?")
    implicitWidth: size
    implicitHeight: size
    Accessible.ignored: true

    Rectangle {
        anchors.fill: parent
        radius: root.size * 0.225
        visible: !(root.hasIcon && root.bleed)
        color: root.hasIcon ? Look.raised : Look.tileFill(root.name !== "" ? root.name : root.mark)
        border.width: 1
        border.color: Look.hairline
    }
    Image {
        id: img
        anchors.fill: parent
        anchors.margins: root.bleed ? 0 : root.size * 0.14
        source: root.iconUrl
        sourceSize: Qt.size(root.size * 2, root.size * 2)
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        smooth: true
        mipmap: true
        visible: status === Image.Ready
    }
    TaosText {
        anchors.centerIn: parent
        visible: !root.hasIcon
        text: root.mark
        font.pixelSize: root.size * 0.42
        font.weight: Font.DemiBold
        color: Look.text
    }
}
