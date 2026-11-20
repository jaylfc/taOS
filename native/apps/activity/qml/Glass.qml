import QtQuick
import Taos.Style 1.0

// A frosted-glass panel: translucent fill, a top sheen, a hairline. The shell
// blurs what is behind it when the compositor allows; the fill reads as glass
// either way.
Rectangle {
    id: g
    radius: Pal.radius
    color: Pal.glassFill
    border.width: 1
    border.color: Pal.hairline
    Rectangle {   // top sheen
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
        height: Math.min(parent.height - 2, 56)
        radius: g.radius - 1
        gradient: Gradient {
            GradientStop { position: 0; color: Pal.sheen }
            GradientStop { position: 1; color: "transparent" }
        }
        opacity: 0.55
    }
}
