import QtQuick
import Taos.Bar

// Monitor glyph for the bar, drawn like the other bar icons: plain shapes in
// the text colour. A screen with a thin frame, a neck and a foot.
Item {
    id: icon
    property color color: Theme.text
    width: 21
    height: 16

    Rectangle {
        x: 2.5; y: 1.5; width: 16; height: 10
        radius: 2
        color: "transparent"
        border.width: 1.5
        border.color: icon.color
    }
    Rectangle { x: 9.75; y: 11.5; width: 1.5; height: 2.5; color: icon.color }
    Rectangle { x: 6.5; y: 14; width: 8; height: 1.5; radius: 0.75; color: icon.color }
}
