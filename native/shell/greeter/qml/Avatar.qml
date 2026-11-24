import QtQuick
import QtQuick.Effects
import QtQuick.Shapes
import Taos.Greeter

// A circular avatar: the user's picture, or a monogram on a soft gradient.
Item {
    id: av
    property real diameter: 96
    property string source: ""
    property string initials: ""
    property color colorA: "#7C86FF"
    property color colorB: "#4A55E0"
    width: diameter; height: diameter

    readonly property bool hasImage: img.status === Image.Ready

    Shape {
        anchors.fill: parent
        visible: !av.hasImage
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: LinearGradient {
                x1: 0; y1: 0; x2: av.diameter; y2: av.diameter
                GradientStop { position: 0.0; color: av.colorA }
                GradientStop { position: 1.0; color: av.colorB }
            }
            PathAngleArc {
                centerX: av.diameter / 2; centerY: av.diameter / 2
                radiusX: av.diameter / 2; radiusY: av.diameter / 2
                startAngle: 0; sweepAngle: 360
            }
        }
    }
    // Soft sheen on the monogram, so it reads as a lit disc and not a flat fill.
    Rectangle {
        visible: !av.hasImage
        anchors.fill: parent
        radius: width / 2
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#26FFFFFF" }
            GradientStop { position: 0.55; color: "#00FFFFFF" }
        }
    }
    Text {
        visible: !av.hasImage
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -av.diameter * 0.01
        text: av.initials
        color: "#FFFFFF"
        font.family: "Inter"
        font.weight: Font.Medium
        font.pixelSize: av.diameter * 0.38
        font.letterSpacing: av.diameter * 0.004
    }
    Image {
        id: img
        anchors.fill: parent
        visible: false
        source: av.source
        asynchronous: false
        fillMode: Image.PreserveAspectCrop
        sourceSize: Qt.size(av.diameter * 2, av.diameter * 2)
        autoTransform: true
    }
    Item {
        id: mask
        visible: false
        layer.enabled: true
        width: av.diameter; height: av.diameter
        Rectangle { anchors.fill: parent; radius: width / 2; color: "black" }
    }
    MultiEffect {
        anchors.fill: parent
        visible: av.hasImage
        source: img
        maskEnabled: true
        maskSource: mask
    }
    // Inner hairline so light avatars keep an edge on a bright wallpaper.
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: "transparent"
        border.width: 1
        border.color: "#2EFFFFFF"
    }
}
