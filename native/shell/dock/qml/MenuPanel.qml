// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Effects
import Taos.Dock

// One panel of the dock menu: the glass, the rows, and (for a row with children) the next panel, to its side. The state
// of the whole menu (which row of which level has the cursor) lives in the overlay, `menu.cur`, so the keyboard and the
// pointer drive the same thing.
Item {
    id: panel
    property var menu: null             // the DockMenu overlay
    property var rows: []               // this level's rows
    property var path: []               // the indices that lead here: [] for the first panel, [3] for the children of row 3
    readonly property int level: path.length
    property real placeAbsX: 0
    property real placeAbsY: 0
    width: level === 0 ? 290 : 232
    height: col.implicitHeight + 12
    readonly property bool dark: menu.dark

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
        shadowOpacity: panel.dark ? 0.45 : 0.28
        shadowBlur: 1.0
        shadowVerticalOffset: 8
    }
    Rectangle {
        anchors.fill: parent
        radius: 12
        color: Qt.rgba(Theme.ground.r, Theme.ground.g, Theme.ground.b, panel.dark ? 0.90 : 0.94)
        border.width: 1
        border.color: Theme.glassStroke
    }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; Accessible.ignored: true }

    Column {
        id: col
        x: 6
        y: 6
        width: parent.width - 12
        spacing: 2
        Repeater {
            model: panel.rows
            delegate: Item {
                id: row
                required property int index
                required property var modelData
                readonly property bool hasChildren: !!modelData.children && modelData.children.length > 0
                readonly property bool open: hasChildren && panel.menu.cur.length > panel.level + 1 && panel.menu.cur[panel.level] === index
                readonly property bool onCursor: panel.menu.cur.length > panel.level && panel.menu.cur[panel.level] === index
                readonly property bool active: onCursor && panel.menu.cur.length === panel.level + 1      // the keyboard is on this row
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
                    color: ma.pressed ? Theme.pressed : (ma.containsMouse || row.onCursor) ? Theme.hover : "transparent"
                    border.width: row.active ? 1 : 0
                    border.color: Theme.focusRing
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        x: 10
                        width: parent.width - 20 - (row.hasChildren || row.modelData.current ? 20 : 0)
                        elide: Text.ElideRight
                        text: row.modelData.text + (row.modelData.minimised ? "  (minimised)" : "")
                        color: row.modelData.current ? Theme.textSecondary : !row.modelData.enabled ? Theme.textTertiary : row.modelData.danger ? Theme.danger : Theme.text
                        font.family: panel.menu.fontFamily
                        font.pixelSize: 13
                    }
                    MenuGlyph {
                        visible: row.hasChildren || row.modelData.current
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        kind: row.hasChildren ? "chevron" : "check"
                        color: row.modelData.current ? Theme.textSecondary : Theme.text
                    }
                    MouseArea {
                        id: ma
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: row.modelData.enabled || row.hasChildren
                        onEntered: panel.menu.hover(panel.level, row.index, row.hasChildren)
                        onClicked: { if (row.modelData.opens) panel.menu.open(panel.level, row.index); else Dock.invokePath(panel.path.concat([row.index])) }
                        Accessible.role: Accessible.MenuItem
                        Accessible.name: row.modelData.accessible ? row.modelData.accessible : row.modelData.text
                        Accessible.description: row.hasChildren ? qsTr("Opens a submenu") : ""
                        Accessible.onPressAction: { if (row.modelData.opens) panel.menu.open(panel.level, row.index); else Dock.invokePath(panel.path.concat([row.index])) }
                    }
                }
                // the next panel, beside this row; flipped to the other side when it would leave the output
                Loader {
                    id: sub
                    active: row.open
                    source: "MenuPanel.qml"      // by name: a component cannot instantiate its own type directly
                    onLoaded: {
                        var c = item
                        c.menu = panel.menu
                        c.rows = Qt.binding(() => row.modelData.children)
                        c.path = Qt.binding(() => panel.path.concat([row.index]))
                        var rowAbsX = () => panel.placeAbsX + col.x
                        var rowAbsY = () => panel.placeAbsY + col.y + row.y
                        var flip = () => panel.placeAbsX + panel.width + 4 + c.width + 8 > panel.menu.width
                        c.x = Qt.binding(() => flip() ? -c.width - 10 : panel.width - 2)
                        c.y = Qt.binding(() => Math.max(8 - rowAbsY(), Math.min(-6, panel.menu.height - 8 - c.height - rowAbsY())))
                        c.placeAbsX = Qt.binding(() => rowAbsX() + c.x)
                        c.placeAbsY = Qt.binding(() => rowAbsY() + c.y)
                    }
                }
            }
        }
    }
}
