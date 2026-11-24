// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Shapes
import Taos.Apps 1.0

// The few line icons Messages needs that the shared Glyph does not have (24 unit grid).
Item {
    id: root
    property string name: "chat"
    property color color: Look.textSecondary
    property real size: 16
    property real stroke: 1.8
    implicitWidth: size
    implicitHeight: size
    Accessible.ignored: true
    readonly property var paths: ({
        "chat": "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
        "hash": "M4 9h16 M4 15h16 M10 3 8 21 M16 3l-2 18",
        "copy": "M9 9h11v11H9z M5 15H4V4h11v1",
        "search": "M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16z m10 18-4.3-4.3",
        "at": "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0z M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8",
        "refresh": "M21 12a9 9 0 1 1-3-6.7L21 8 M21 3v5h-5",
        "bot": "M12 8V4H8 M4 8h16v12H4z M2 14h2 M20 14h2 M15 13v2 M9 13v2",
        "x": "M18 6 6 18 M6 6l12 12"
    })
    Shape {
        width: 24; height: 24
        scale: root.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: root.color
            strokeWidth: root.stroke
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: root.paths[root.name] || "" }
        }
    }
}
