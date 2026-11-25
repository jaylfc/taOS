// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// A QR code drawn from the rows the engine encoder gives ("0" and "1"). Always dark on light so a camera reads it,
// in every theme: the two colours are the theme's ground and text, swapped when the theme is dark.
Item {
    id: root
    property var rows: []
    property string accessibleName: qsTr("QR code")
    readonly property int n: rows.length
    readonly property int quiet: 2
    implicitWidth: 220
    implicitHeight: 220
    Accessible.role: Accessible.Graphic
    Accessible.name: accessibleName
    readonly property color paper: Look.dark ? Look.text : Look.ground
    readonly property color ink: Look.dark ? Look.ground : Look.text
    Rectangle { anchors.fill: parent; radius: Look.radiusControl; color: root.paper }
    Canvas {
        id: cv
        anchors.fill: parent
        renderTarget: Canvas.Image
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            if (root.n === 0) return
            const cells = root.n + root.quiet * 2
            const s = Math.floor(Math.min(width, height) / cells)
            const off = Math.floor((Math.min(width, height) - s * cells) / 2) + root.quiet * s
            ctx.fillStyle = root.ink
            for (let y = 0; y < root.n; y++) {
                const row = root.rows[y]
                for (let x = 0; x < root.n; x++)
                    if (row.charAt(x) === "1") ctx.fillRect(off + x * s, off + y * s, s, s)
            }
        }
        Connections { target: root; function onRowsChanged() { cv.requestPaint() } function onInkChanged() { cv.requestPaint() } }
        onWidthChanged: requestPaint()
    }
}
