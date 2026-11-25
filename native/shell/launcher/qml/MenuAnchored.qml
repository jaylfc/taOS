// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// layout.launcher "anchored": the classic two pane start menu at the taskbar: every app (what you use first, then by
// category) on the left, the pinned grid on the right, the person and power along the bottom.
Item {
    id: m
    readonly property real footH: Style.space(52)
    readonly property real leftW: Math.round(width * 0.5)
    ActionsList { visible: Launcher.actionsMode; anchors.fill: parent; anchors.bottomMargin: m.footH }
    Item {
        visible: !Launcher.actionsMode
        anchors.fill: parent
        anchors.bottomMargin: m.footH
        AllApps { width: m.leftW; height: parent.height }
        Rectangle { x: m.leftW; width: Math.max(1, Style.borderWidth); height: parent.height - Style.space(16); y: Style.space(8); color: Style.hairline }
        SectionLabel { id: pl; x: m.leftW + Style.space(16); y: Style.space(10); height: Style.space(24); text: qsTr("Pinned") }
        PinnedGrid {
            x: m.leftW + Style.space(8); y: pl.y + pl.height + Style.space(4)
            width: parent.width - m.leftW - Style.space(16)
            height: parent.height - y - Style.space(8)
            cellW: Style.space(88); cellH: Style.space(92)
        }
    }
    MenuFooter { compact: true; y: parent.height - height; width: parent.width; height: m.footH }
    onVisibleChanged: if (visible) Launcher.menu.setLayout("anchored")
    Component.onCompleted: Launcher.menu.setLayout("anchored")
}
