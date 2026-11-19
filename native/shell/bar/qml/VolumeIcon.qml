import QtQuick
import Taos.Bar

// Speaker glyph. level: none (no output: dim, struck through), muted (dim,
// struck through), low, medium, high (one to three waves).
Item {
    id: icon
    property string level: "high"
    property color color: Theme.text
    width: 21
    height: 16
    readonly property bool silent: level === "none" || level === "muted"
    readonly property color glyph: silent ? Theme.textTertiary : color

    Rectangle { x: 2; y: 5; width: 4; height: 6; color: icon.glyph; radius: 1 }
    Rectangle { x: 3.5; y: 3.5; width: 7; height: 7; rotation: 45; color: icon.glyph; radius: 1; transformOrigin: Item.Center }
    Rectangle { visible: !icon.silent; x: 13; y: 6; width: 1.5; height: 4; radius: 1; color: icon.glyph }
    Rectangle { visible: icon.level === "medium" || icon.level === "high"; x: 15.5; y: 4; width: 1.5; height: 8; radius: 1; color: icon.glyph }
    Rectangle { visible: icon.level === "high"; x: 18; y: 2; width: 1.5; height: 12; radius: 1; color: icon.glyph }
    Rectangle {
        visible: icon.silent
        anchors.centerIn: parent
        width: 22; height: 1.5; radius: 1; rotation: -40
        color: icon.level === "none" ? Theme.textTertiary : Theme.danger
    }
}
