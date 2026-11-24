// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Shapes
import Taos.Bar

// A quiet arc that turns while something is happening. With reduced motion it
// turns very slowly rather than stopping, so "busy" is still visible.
Item {
    id: sp
    property color color: Theme.textSecondary
    property bool running: true
    width: 14
    height: 14
    visible: running
    Shape {
        id: shape
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: sp.color
            strokeWidth: 1.8
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: sp.width / 2; centerY: sp.height / 2
                radiusX: sp.width / 2 - 1.5; radiusY: sp.height / 2 - 1.5
                startAngle: -90; sweepAngle: 270
            }
        }
    }
    RotationAnimator on rotation {
        running: sp.running && sp.visible
        from: 0; to: 360
        duration: Theme.reduceMotion ? 2400 : 900
        loops: Animation.Infinite
    }
}
