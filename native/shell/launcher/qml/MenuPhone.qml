// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// A narrow screen (a phone): the menu is the whole screen. Pinned apps as a grid of four, then every app by letter with an
// index rail down the side for quick jumps. The same model as every other layout.
Item {
    id: m
    readonly property real footH: Style.space(48)
    ActionsList { visible: Launcher.actionsMode; anchors.fill: parent; anchors.bottomMargin: m.footH }
    Item {
        visible: !Launcher.actionsMode
        anchors.fill: parent
        anchors.bottomMargin: m.footH
        SectionLabel { id: pl; x: Style.space(16); y: Style.space(8); height: Style.space(24); text: qsTr("Pinned") }
        PinnedGrid {
            id: pg
            x: Style.space(8); y: pl.y + pl.height
            width: parent.width - Style.space(16)
            cellW: width / 4; cellH: Style.space(92); showGroup: false
            height: Math.min(cellH * 2, Math.max(cellH, Math.ceil(Launcher.menu.pinnedCount / columns) * cellH))
        }
        AllApps { y: pg.y + pg.height + Style.space(4); width: parent.width - Style.space(24); height: parent.height - y }
        // the index rail: tap or drag a letter to jump
        Column {
            id: rail
            visible: Launcher.menu.sort === "az"
            anchors.right: parent.right
            y: pg.y + pg.height + Style.space(40)
            width: Style.space(24)
            Repeater {
                model: "ABCDEFGHIJKLMNOPQRSTUVWXYZ#".split("")
                delegate: Text {
                    required property string modelData
                    width: rail.width; height: Math.min(Style.space(18), (m.height - y0 - m.footH - Style.space(48)) / 27)
                    readonly property real y0: pg.y + pg.height + Style.space(40)
                    horizontalAlignment: Text.AlignHCenter
                    text: modelData
                    color: Style.textSecondary
                    font.family: Style.fontDataStack
                    font.pixelSize: Style.fontSize - 3
                    Accessible.ignored: true
                }
            }
        }
    }
    MenuFooter { compact: true; y: parent.height - height; width: parent.width; height: m.footH }
    onVisibleChanged: if (visible) Launcher.menu.setLayout("phone")
    Component.onCompleted: Launcher.menu.setLayout("phone")
}
