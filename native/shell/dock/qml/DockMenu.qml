// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Effects
import Taos.Dock

// The right-click menu and the window list: a clear overlay over the output (a click anywhere closes
// it) with a glass panel next to the item. Up, Down, Enter and Escape work.
FocusScope {
    id: root
    property real anchorX: 0
    property real anchorY: 0
    property string edge: "bottom"
    property real clearance: Dock.iconSize / 2 + 24      // from the anchor to the panel; the taskbar passes a smaller one
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    readonly property bool dark: Theme.variant !== "light"
    property int current: -1
    property int subCurrent: -1      // -1: the row itself; 0..2: one of its inline buttons
    focus: true
    Accessible.role: Accessible.Pane
    Accessible.name: "Dock menu"

    function step(d) {
        var n = Dock.menuRows.length
        for (var i = 1; i <= n; ++i) {
            var k = ((current < 0 ? (d > 0 ? -1 : n) : current) + d * i + n * 4) % n
            var r = Dock.menuRows[k]
            if (!r.separator && r.enabled) { current = k; return }
        }
    }
    Keys.onEscapePressed: Dock.closeMenu()
    Keys.onDownPressed: { subCurrent = -1; step(1) }
    Keys.onUpPressed: { subCurrent = -1; step(-1) }
    function nSubs() { return current >= 0 && Dock.menuRows[current].subs ? Dock.menuRows[current].subs.length : 0 }
    Keys.onRightPressed: { if (subCurrent + 1 < nSubs()) subCurrent = subCurrent + 1 }
    Keys.onLeftPressed: { if (subCurrent >= 0) subCurrent = subCurrent - 1 }
    function activate() {
        if (current < 0) return
        if (subCurrent >= 0) Dock.invokeSub(current, subCurrent); else Dock.invokeMenu(current)
    }
    Keys.onReturnPressed: activate()
    Keys.onEnterPressed: activate()
    Component.onCompleted: { forceActiveFocus(); if (!Theme.reduceMotion) openAnim.start(); else panel.opacity = 1 }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onPressed: Dock.closeMenu()
        Accessible.ignored: true
    }

    Item {
        id: panel
        width: 290
        height: col.implicitHeight + 12
        opacity: 0
        x: edge === "left" ? root.anchorX + Dock.iconSize / 2 + 18
         : edge === "right" ? root.anchorX - width - Dock.iconSize / 2 - 18
         : Math.max(8, Math.min(root.width - width - 8, root.anchorX - width / 2))
        y: edge === "bottom" ? root.anchorY - root.clearance - height
         : edge === "top" ? root.anchorY + root.clearance
         : Math.max(8, Math.min(root.height - height - 8, root.anchorY - height / 2))
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }
        NumberAnimation { id: openAnim; target: panel; property: "opacity"; from: 0; to: 1; duration: 130; easing.type: Easing.OutCubic }

        Rectangle {
            id: shadowSource
            anchors.fill: parent
            radius: 12
            color: Theme.ground
            visible: false
            layer.enabled: true
        }
        MultiEffect {
            anchors.fill: shadowSource
            source: shadowSource
            autoPaddingEnabled: true
            shadowEnabled: true
            shadowColor: "black"
            shadowOpacity: root.dark ? 0.45 : 0.28
            shadowBlur: 1.0
            shadowVerticalOffset: 8
        }
        Rectangle {
            anchors.fill: parent
            radius: 12
            color: Qt.rgba(Theme.ground.r, Theme.ground.g, Theme.ground.b, root.dark ? 0.90 : 0.94)
            border.width: 1
            border.color: Theme.glassStroke
        }
        Column {
            id: col
            x: 6
            y: 6
            width: parent.width - 12
            spacing: 2
            Repeater {
                model: Dock.menuRows
                delegate: Item {
                    id: row
                    required property int index
                    required property var modelData
                    width: col.width
                    height: modelData.separator ? 9 : 32
                    Rectangle {
                        visible: row.modelData.separator
                        anchors.verticalCenter: parent.verticalCenter
                        x: 8
                        width: parent.width - 16
                        height: 1
                        color: Theme.hairline
                    }
                    Rectangle {
                        visible: !row.modelData.separator
                        anchors.fill: parent
                        radius: 8
                        color: ma.pressed ? Theme.pressed : (ma.containsMouse || root.current === row.index) ? Theme.hover : "transparent"
                        border.width: root.current === row.index ? 1 : 0
                        border.color: Theme.focusRing
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 10
                            width: parent.width - 20 - (row.modelData.subs && row.modelData.subs.length ? row.modelData.subs.length * 28 : 0)
                            elide: Text.ElideRight
                            text: row.modelData.text + (row.modelData.minimised ? "  (minimised)" : "")
                            color: !row.modelData.enabled ? Theme.textTertiary : row.modelData.danger ? Theme.danger : Theme.text
                            font.family: root.fontFamily
                            font.pixelSize: 13
                        }
                        Row {   // inline window actions: minimise or restore, full size, close
                            visible: row.modelData.subs && row.modelData.subs.length > 0
                            anchors.right: parent.right
                            anchors.rightMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Repeater {
                                model: row.modelData.subs ? row.modelData.subs : []
                                delegate: Rectangle {
                                    id: sb
                                    required property int index
                                    required property var modelData
                                    width: 26
                                    height: 26
                                    radius: 7
                                    opacity: modelData.enabled ? 1 : 0.4
                                    color: sma.pressed ? Theme.pressed : sma.containsMouse ? Theme.hover : "transparent"
                                    border.width: root.current === row.index && root.subCurrent === sb.index ? 2 : 0
                                    border.color: Theme.focusRing
                                    MenuGlyph {
                                        anchors.centerIn: parent
                                        kind: sb.modelData.glyph
                                        color: sb.modelData.glyph === "close" ? Theme.danger : Theme.text
                                    }
                                    MouseArea {
                                        id: sma
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        enabled: sb.modelData.enabled
                                        onEntered: { root.current = row.index; root.subCurrent = sb.index }
                                        onClicked: Dock.invokeSub(row.index, sb.index)
                                        Accessible.role: Accessible.Button
                                        Accessible.name: sb.modelData.accessible
                                        Accessible.onPressAction: Dock.invokeSub(row.index, sb.index)
                                    }
                                }
                            }
                        }
                        MouseArea {
                            id: ma
                            anchors.fill: parent
                            z: -1
                            hoverEnabled: true
                            enabled: row.modelData.enabled
                            onEntered: { root.current = row.index; root.subCurrent = -1 }
                            onClicked: Dock.invokeMenu(row.index)
                            Accessible.role: Accessible.MenuItem
                            Accessible.name: row.modelData.accessible ? row.modelData.accessible : row.modelData.text
                            Accessible.onPressAction: Dock.invokeMenu(row.index)
                        }
                    }
                }
            }
        }
    }
}
