import QtQuick
import QtQuick.Shapes
import Taos.Bar

// The Bluetooth rune as one stroked path.
Item {
    id: g
    property color color: Theme.text
    width: 14
    height: 16
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: g.color
            strokeWidth: 1.5
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            startX: 2.5; startY: 4.6
            PathLine { x: 11; y: 11.4 }
            PathLine { x: 7; y: 15 }
            PathLine { x: 7; y: 1 }
            PathLine { x: 11; y: 4.6 }
            PathLine { x: 2.5; y: 11.4 }
        }
    }
}
