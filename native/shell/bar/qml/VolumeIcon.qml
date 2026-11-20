import QtQuick
import Taos.Bar

// Speaker glyph; muted: dimmed and struck through.
Item {
    id: icon
    property bool muted: false
    property color color: Theme.text
    width: 18
    height: 16
    readonly property color glyph: muted ? Theme.textTertiary : color

    Rectangle { x: 2; y: 5; width: 4; height: 6; color: icon.glyph; radius: 1 }
    Rectangle {
        x: 3.5; y: 3.5; width: 7; height: 7; rotation: 45
        color: icon.glyph
        radius: 1
        transformOrigin: Item.Center
        // Cone: only the right half is visible, the left is covered by the box above.
    }
    Rectangle { visible: !icon.muted; x: 13; y: 6; width: 1.5; height: 4; radius: 1; color: icon.glyph }
    Rectangle { visible: !icon.muted; x: 15.5; y: 4; width: 1.5; height: 8; radius: 1; color: icon.glyph }
    Rectangle {
        visible: icon.muted
        anchors.centerIn: parent
        width: 20; height: 1.5; radius: 1; rotation: -40
        color: Theme.danger
    }
}
