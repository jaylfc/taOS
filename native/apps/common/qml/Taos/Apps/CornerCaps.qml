// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Shapes
import Taos.Apps 1.0

// Rounds the corners of whatever sits under it without a shader (so it also draws on the software renderer): a
// quarter-disc cut-out in the colour of what is behind. Put it over an image and give it that colour.
Item {
    id: root
    property real radius: Look.radiusCard
    property color color: Look.surface
    property bool topLeft: true
    property bool topRight: true
    property bool bottomLeft: false
    property bool bottomRight: false
    anchors.fill: parent
    Accessible.ignored: true

    component Cap: Shape {
        id: cap
        property int turns: 0
        width: root.radius; height: root.radius
        rotation: turns * 90
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        ShapePath {
            fillColor: root.color
            strokeWidth: -1
            startX: 0; startY: 0
            PathLine { x: cap.width; y: 0 }
            PathArc { x: 0; y: cap.height; radiusX: cap.width; radiusY: cap.height; direction: PathArc.Counterclockwise }
            PathLine { x: 0; y: 0 }
        }
    }
    Cap { visible: root.topLeft;     turns: 0; x: 0;                      y: 0 }
    Cap { visible: root.topRight;    turns: 1; x: root.width - width;     y: 0 }
    Cap { visible: root.bottomRight; turns: 2; x: root.width - width;     y: root.height - height }
    Cap { visible: root.bottomLeft;  turns: 3; x: 0;                      y: root.height - height }
}
