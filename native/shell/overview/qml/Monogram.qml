import QtQuick
import QtQuick.Shapes
import Taos.Overview

// App tile for windows whose app has no icon: a lit disc with the initials, in the greeter's
// avatar style. The hue follows the name so different apps stay apart.
Item {
    id: mg
    property real diameter: 48
    property string name: ""
    readonly property string initials: {
        const parts = name.replace(/[^A-Za-z0-9 ._-]/g, "").split(/[ ._-]+/).filter(s => s.length > 0);
        if (parts.length === 0) return "?";
        if (parts.length === 1) return parts[0].substring(0, 2).charAt(0).toUpperCase() + parts[0].substring(1, 2).toLowerCase();
        return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
    }
    readonly property real hue: {
        let h = 0;
        for (let i = 0; i < name.length; ++i) h = (h * 31 + name.charCodeAt(i)) % 360;
        return h / 360;
    }
    width: diameter; height: diameter
    Accessible.ignored: true

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: LinearGradient {
                x1: 0; y1: 0; x2: mg.diameter; y2: mg.diameter
                GradientStop { position: 0.0; color: Qt.hsla(mg.hue, 0.62, 0.66, 1) }
                GradientStop { position: 1.0; color: Qt.hsla(mg.hue, 0.66, 0.46, 1) }
            }
            PathAngleArc {
                centerX: mg.diameter / 2; centerY: mg.diameter / 2
                radiusX: mg.diameter / 2; radiusY: mg.diameter / 2
                startAngle: 0; sweepAngle: 360
            }
        }
    }
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#26FFFFFF" }
            GradientStop { position: 0.55; color: "#00FFFFFF" }
        }
    }
    Text {
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -mg.diameter * 0.01
        text: mg.initials
        color: "#FFFFFF"
        font.family: "Inter"
        font.weight: Font.Medium
        font.pixelSize: mg.diameter * 0.38
    }
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: "transparent"
        border.width: 1
        border.color: "#2EFFFFFF"
    }
}
