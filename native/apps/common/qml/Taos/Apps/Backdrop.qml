import QtQuick
import QtQuick.Shapes
import Taos.Apps 1.0

// The window ground: the theme ground with two soft accent glows, so glass
// panels above it have something to be translucent over. Plain shapes, no
// shader effect, so it renders the same on every backend.
Rectangle {
    id: root
    color: Look.ground
    clip: true
    component Glow: Shape {
        id: glow
        property real cx: 0
        property real cy: 0
        property real radius: 300
        property color tint: Look.accent
        anchors.fill: parent
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: glow.cx; centerY: glow.cy; centerRadius: glow.radius
                focalX: glow.cx; focalY: glow.cy
                GradientStop { position: 0; color: glow.tint }
                GradientStop { position: 1; color: "transparent" }
            }
            startX: 0; startY: 0
            PathLine { x: glow.width; y: 0 }
            PathLine { x: glow.width; y: glow.height }
            PathLine { x: 0; y: glow.height }
            PathLine { x: 0; y: 0 }
        }
    }
    Glow { cx: root.width * 0.12; cy: -20; radius: Math.max(root.width, 360) * 0.9
           tint: Look.tint(Look.accent, Look.dark ? 0.22 : 0.15) }
    Glow { cx: root.width * 0.95; cy: root.height * 1.05; radius: Math.max(root.width, 360) * 0.8
           tint: Look.tint(Look.dark ? "#B05CFF" : "#7C86FF", Look.dark ? 0.14 : 0.10) }
}
