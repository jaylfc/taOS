import QtQuick
import QtQuick.Shapes
import Taos.Bar

Item {
    id: g
    property color color: Theme.text
    width: 14
    height: 14
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: g.color
            strokeWidth: 1.9
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            startX: 2; startY: 7.5
            PathLine { x: 5.6; y: 11 }
            PathLine { x: 12; y: 3.2 }
        }
    }
}
