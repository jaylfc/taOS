// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Shapes
import Taos.Style 1.0

// Line icons for the sidebar, drawn from 24 x 24 paths in the theme's text colour.
Item {
    id: icon
    property string name: ""
    property color color: TaosStyle.textSecondary
    property real size: 18
    width: size; height: size
    readonly property var paths: ({
        appearance: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z M12 3v18 M12 3a9 9 0 0 1 0 18z",
        display: "M3 5h18v11H3z M8 20h8 M12 16v4",
        dock: "M3 6h18v12H3z M6 15h12",
        desktop: "M4 5h10v8H4z M10 11h10v8H10z",
        sound: "M4 9v6h4l5 4V5L8 9z M16.5 9a4 4 0 0 1 0 6 M19 6.5a8 8 0 0 1 0 11",
        network: "M2.5 9a14 14 0 0 1 19 0 M5.5 12.5a10 10 0 0 1 13 0 M8.5 16a5.5 5.5 0 0 1 7 0 M12 19.5h.01",
        apps: "M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z",
        about: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z M12 11v6 M12 7.5h.01",
        search: "M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14z M16 16l5 5",
        back: "M15 5l-7 7l7 7"
    })
    Shape {
        anchors.centerIn: parent
        width: 24; height: 24
        scale: icon.size / 24
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: icon.color
            strokeWidth: 1.7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: icon.paths[icon.name] || "" }
        }
    }
    Accessible.ignored: true
}
