// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Dock

// The small window-action glyphs of the dock menu, drawn from rectangles in the theme's text colour.
Item {
    id: g
    property string kind: "min"       // min | restore | full | unfull | close
    property color color: Theme.text
    width: 14
    height: 14
    Rectangle { visible: g.kind === "min"; x: 2; y: 10; width: 10; height: 2; radius: 1; color: g.color }
    Rectangle {
        visible: g.kind === "restore"
        x: 2; y: 2; width: 10; height: 10; radius: 2
        color: "transparent"; border.width: 1.5; border.color: g.color
    }
    Rectangle {
        visible: g.kind === "full"
        x: 1; y: 1; width: 12; height: 12; radius: 2
        color: "transparent"; border.width: 1.5; border.color: g.color
    }
    Rectangle {   // restore size: a smaller frame inside
        visible: g.kind === "unfull"
        x: 1; y: 1; width: 12; height: 12; radius: 2
        color: "transparent"; border.width: 1; border.color: g.color
        Rectangle { x: 3; y: 3; width: 6; height: 6; radius: 1; color: "transparent"; border.width: 1.5; border.color: g.color }
    }
    Rectangle { visible: g.kind === "close"; anchors.centerIn: parent; width: 14; height: 1.8; radius: 1; rotation: 45; color: g.color }
    Rectangle { visible: g.kind === "close"; anchors.centerIn: parent; width: 14; height: 1.8; radius: 1; rotation: -45; color: g.color }
}
