import QtQuick
import QtQuick.Shapes
import Taos.Bar

// Standard speaker glyph: a solid speaker cone with one to three sound waves.
// level: none (no output) and muted draw the cone, dimmed, with a diagonal slash through it;
// low, medium, high draw one, two or three waves.
Item {
    id: icon
    property string level: "high"
    property color color: Theme.text
    width: 21
    height: 16
    readonly property bool silent: level === "none" || level === "muted"
    readonly property color glyph: silent ? Theme.textTertiary : color
    readonly property real stroke: 1.5

    Shape {
        anchors.fill: parent
        antialiasing: true
        preferredRendererType: Shape.CurveRenderer

        // Speaker cone: a small box that flares out to the right.
        ShapePath {
            fillColor: icon.glyph
            strokeColor: icon.glyph
            strokeWidth: 1
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: "M 2.5 6 L 5.2 6 L 9 2.8 L 9 13.2 L 5.2 10 L 2.5 10 Z" }
        }
        // Waves, centred on the mouth of the cone.
        ShapePath {
            fillColor: "transparent"
            strokeColor: !icon.silent ? icon.glyph : "transparent"
            strokeWidth: icon.stroke
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M 11.4 5.7 A 3.3 3.3 0 0 1 11.4 10.3" }
        }
        ShapePath {
            fillColor: "transparent"
            strokeColor: (icon.level === "medium" || icon.level === "high") ? icon.glyph : "transparent"
            strokeWidth: icon.stroke
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M 13.5 3.9 A 5.9 5.9 0 0 1 13.5 12.1" }
        }
        ShapePath {
            fillColor: "transparent"
            strokeColor: icon.level === "high" ? icon.glyph : "transparent"
            strokeWidth: icon.stroke
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M 15.6 2.1 A 8.5 8.5 0 0 1 15.6 13.9" }
        }
        // Slash for muted or no output.
        ShapePath {
            fillColor: "transparent"
            strokeColor: icon.silent ? icon.glyph : "transparent"
            strokeWidth: icon.stroke
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M 3.2 1.8 L 16.8 14.2" }
        }
    }
}
