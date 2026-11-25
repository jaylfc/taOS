// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// layout.launcher "centered": a full panel. Pinned apps and the ones used lately on the left, every app on the right.
Item {
    id: m
    readonly property real footH: Style.space(56)
    readonly property real leftW: Math.round(width * 0.64)
    ActionsList { visible: Launcher.actionsMode; anchors.fill: parent; anchors.bottomMargin: m.footH }
    Item {
        visible: !Launcher.actionsMode
        anchors.fill: parent
        anchors.bottomMargin: m.footH
        Item {
            id: left
            width: m.leftW
            height: parent.height
            SectionLabel { id: pl; x: Style.space(20); y: Style.space(12); height: Style.space(24); text: qsTr("Pinned") }
            PinnedGrid {
                id: pg
                x: Style.space(12); y: pl.y + pl.height + Style.space(4)
                width: left.width - Style.space(24)
                cellW: Style.space(92); cellH: Style.space(96)
                height: Math.min(Style.space(96) * 3, Math.max(Style.space(96), Math.ceil(Launcher.menu.pinnedCount / columns) * cellH))
            }
            SectionLabel { id: rl; visible: rr.visible; x: Style.space(20); y: pg.y + pg.height + Style.space(8); height: Style.space(24); text: qsTr("Recent") }
            RecentRow { id: rr; x: Style.space(12); y: rl.y + rl.height; width: left.width - Style.space(24); height: implicitHeight; cellW: Style.space(92) }
        }
        Rectangle { x: m.leftW; width: Math.max(1, Style.borderWidth); height: parent.height - Style.space(16); y: Style.space(8); color: Style.hairline }
        AllApps { x: m.leftW + 1; width: parent.width - m.leftW - 1; height: parent.height }
    }
    MenuFooter { y: parent.height - height; width: parent.width; height: m.footH }
    onVisibleChanged: if (visible) Launcher.menu.setLayout("centered")
    Component.onCompleted: Launcher.menu.setLayout("centered")
}
