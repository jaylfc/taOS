import QtQuick
import QtQuick.Shapes
import Taos.Apps 1.0

// Line icons of the Agents app (24 unit grid). Decorative: the control that
// holds one carries the accessible name.
Item {
    id: root
    property string name: "bot"
    property color color: Look.textSecondary
    property real size: 16
    property real stroke: 1.8
    implicitWidth: size
    implicitHeight: size
    Accessible.ignored: true
    readonly property var paths: ({
        "bot": "M12 8V4H8 M5 8h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z M9 13v2 M15 13v2",
        "play": "M7 4l13 8-13 8z",
        "pause": "M7 4h3v16H7z M14 4h3v16h-3z",
        "stop": "M6 6h12v12H6z",
        "restart": "M21 12a9 9 0 1 1-3-6.7 M21 3v6h-6",
        "message": "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
        "search": "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M21 21l-4.3-4.3",
        "phone": "M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z M12 18h.01",
        "server": "M4 3h16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M4 13h16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1z M7 7h.01 M7 17h.01",
        "monitor": "M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z M8 21h8 M12 17v4",
        "back": "M19 12H5 M12 19l-7-7 7-7",
        "external": "M15 3h6v6 M10 14 21 3 M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6",
        "alert": "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3 M12 9v4 M12 17h.01",
        "lock": "M5 11h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z M8 11V7a4 4 0 0 1 8 0v4",
        "layers": "m12 2 10 5-10 5L2 7z M2 17l10 5 10-5 M2 12l10 5 10-5"
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
