// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// Banner artwork: the picture when there is one (fades in), else a quiet gradient picked from the name inside the
// theme's neutral range. `backdrop` is the colour behind the item, used to round its corners.
Item {
    id: root
    property url source: ""
    property string name: ""
    property real radius: Look.radiusCard
    property color backdrop: Look.surface
    property bool roundBottom: false
    readonly property bool ready: img.status === Image.Ready
    clip: true
    Accessible.ignored: true

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Look.tileFill(root.name) }
            GradientStop { position: 1; color: Look.tint(Look.text, 0.03) }
        }
    }
    Image {
        id: img
        anchors.fill: parent
        source: root.source
        sourceSize: Qt.size(Math.max(1, root.width) * 2, Math.max(1, root.height) * 2)
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        smooth: true
        opacity: status === Image.Ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Look.base; easing.type: Look.ease } }
    }
    CornerCaps { radius: root.radius; color: root.backdrop; bottomLeft: root.roundBottom; bottomRight: root.roundBottom }
}
