// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// The surface of a bar or of the taskbar tray, in the style the theme asks for (skin barSurface / dockSurface):
// translucent (today's look), solid (opaque ground) or raised (opaque surface with a lit top edge and a shaded bottom
// edge). `lineSide` is the edge the hairline sits on, the one that faces the desktop.
Item {
    id: s
    property string kind: Theme.barSurface
    property string lineSide: "bottom"      // top | bottom
    readonly property int bw: Theme.borderWidth
    // How far the content must stay clear of the hairline or the bevel.
    readonly property int insetTop: kind === "raised" ? bw : (lineSide === "top" ? bw : 0)
    readonly property int insetBottom: kind === "raised" ? bw : (lineSide === "bottom" ? bw : 0)

    Rectangle {
        anchors.fill: parent
        color: s.kind === "solid" ? Theme.ground : s.kind === "raised" ? Theme.surface : Theme.barBackground
    }
    Rectangle {   // hairline
        visible: s.kind !== "raised" && s.bw > 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: s.lineSide === "top" ? parent.top : undefined
        anchors.bottom: s.lineSide === "bottom" ? parent.bottom : undefined
        height: s.bw
        color: Theme.stroke
    }
    Rectangle {   // bevel: lit edge on top, shaded edge below
        visible: s.kind === "raised" && s.bw > 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: s.bw
        color: Theme.bevelLight
    }
    Rectangle {
        visible: s.kind === "raised" && s.bw > 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: s.bw
        color: Theme.bevelDark
    }
}
