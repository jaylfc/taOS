import QtQuick
import QtQuick.Shapes
import Taos.Bar

// Small line glyphs for the device kinds: usb, bluetooth, hdmi, analog,
// speaker, headphones, mic. Drawn, not loaded.
Item {
    id: g
    property string kind: "speaker"
    property color color: Theme.textSecondary
    width: 18
    height: 16

    BluetoothGlyph { visible: g.kind === "bluetooth"; anchors.centerIn: parent; color: g.color }
    // USB: a stem with a circle, a square and an arrow head.
    Shape {
        visible: g.kind === "usb"
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: g.color; strokeWidth: 1.4; fillColor: "transparent"; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
            startX: 3; startY: 8
            PathLine { x: 15; y: 8 }
            PathMove { x: 9; y: 8 }
            PathLine { x: 9; y: 3 }
            PathMove { x: 5; y: 8 }
            PathLine { x: 5; y: 12 }
            PathMove { x: 9; y: 8 }
            PathLine { x: 12; y: 5 }
            PathLine { x: 14; y: 5 }
        }
    }
    // HDMI: a trapezoid plug.
    Shape {
        visible: g.kind === "hdmi"
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: g.color; strokeWidth: 1.4; fillColor: "transparent"; joinStyle: ShapePath.RoundJoin
            startX: 2; startY: 4
            PathLine { x: 16; y: 4 }
            PathLine { x: 16; y: 8.5 }
            PathLine { x: 13.5; y: 12 }
            PathLine { x: 4.5; y: 12 }
            PathLine { x: 2; y: 8.5 }
            PathLine { x: 2; y: 4 }
        }
    }
    // Headphones: an arc and two cups.
    Item {
        visible: g.kind === "headphones"
        anchors.fill: parent
        Rectangle { x: 3; y: 2; width: 12; height: 12; radius: 6; color: "transparent"; border.width: 1.4; border.color: g.color }
        Rectangle { x: 0; y: 8; width: 18; height: 8; color: Theme.ground; opacity: 0 }
        Rectangle { x: 1.5; y: 8; width: 4; height: 6; radius: 1.5; color: g.color }
        Rectangle { x: 12.5; y: 8; width: 4; height: 6; radius: 1.5; color: g.color }
    }
    // Microphone: a capsule and a stand.
    Item {
        visible: g.kind === "mic"
        anchors.fill: parent
        Rectangle { x: 6.5; y: 1; width: 5; height: 9; radius: 2.5; color: "transparent"; border.width: 1.4; border.color: g.color }
        Rectangle { x: 4; y: 6; width: 10; height: 6; radius: 5; color: "transparent"; border.width: 1.4; border.color: g.color }
        Rectangle { x: 8.3; y: 12; width: 1.4; height: 3; color: g.color }
    }
    // Speaker (and analog jack): the bar's speaker glyph.
    VolumeIcon { visible: g.kind === "speaker" || g.kind === "analog"; level: "medium"; color: g.color; anchors.centerIn: parent; scale: 0.85 }
}
