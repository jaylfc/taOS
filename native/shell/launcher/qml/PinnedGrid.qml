// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// The pinned apps as a grid of tiles. Columns follow the width; the menu's cursor is told how many there are.
Item {
    id: pg
    property real cellW: Style.space(88)
    property real cellH: Style.space(88)
    property bool showGroup: false
    readonly property int columns: Math.max(1, Math.floor(width / cellW))
    onColumnsChanged: Launcher.menu.setColumns("pinned", columns)
    Component.onCompleted: Launcher.menu.setColumns("pinned", columns)
    function ensureVisible() { if (Launcher.menu.zone === "pinned") grid.positionViewAtIndex(Launcher.menu.index, GridView.Contain) }
    Connections { target: Launcher.menu; function onCursorChanged() { pg.ensureVisible() } }

    GridView {
        id: grid
        anchors.fill: parent
        cellWidth: pg.width / pg.columns
        cellHeight: pg.cellH
        clip: true
        interactive: contentHeight > height
        boundsBehavior: Flickable.StopAtBounds
        model: Launcher.menu.pinned
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Pinned apps")
        delegate: AppTile {
            required property int index
            required property string appId
            required property string name
            required property string icon
            required property bool hasIcon
            required property string group
            width: grid.cellWidth
            height: grid.cellHeight
            selected: Launcher.menu.zone === "pinned" && Launcher.menu.index === index
            showGroup: pg.showGroup
            iconSize: Style.space(40)
            onActivated: Launcher.menu.activate("pinned", index)
            onContextRequested: Launcher.menu.openContextFor("pinned", index)
            onHovered: Launcher.menu.hover("pinned", index)
        }
    }
    Text {
        anchors.centerIn: parent
        visible: Launcher.menu.pinnedCount === 0
        width: parent.width - Style.space(24)
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        text: qsTr("Nothing is pinned yet. Right click an app and choose Pin to menu.")
        color: Style.textSecondary
        font.family: Style.fontStack
        font.pixelSize: Style.fontSize
    }
}
