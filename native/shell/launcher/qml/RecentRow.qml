// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// The apps used most lately that are not pinned, as one row of small tiles.
Item {
    id: rr
    property real cellW: Style.space(88)
    readonly property int columns: Math.max(1, Math.floor(width / cellW))
    onColumnsChanged: Launcher.menu.setColumns("recent", columns)
    Component.onCompleted: Launcher.menu.setColumns("recent", columns)
    implicitHeight: Launcher.menu.recentCount > 0 ? Style.space(104) : 0
    visible: Launcher.menu.recentCount > 0
    Row {
        anchors.fill: parent
        Repeater {
            model: Launcher.menu.recent
            delegate: AppTile {
                required property int index
                required property string appId
                required property string name
                required property string icon
                required property bool hasIcon
                required property string group
                width: rr.width / rr.columns
                height: rr.height
                iconSize: Style.space(32)
                showGroup: false
                visible: index < rr.columns
                selected: Launcher.menu.zone === "recent" && Launcher.menu.index === index
                onActivated: Launcher.menu.activate("recent", index)
                onContextRequested: Launcher.menu.openContextFor("recent", index)
                onHovered: Launcher.menu.hover("recent", index)
            }
        }
    }
}
