// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// layout.launcher "ring": the pinned apps on a ring around the middle, the name of the one you are on in the centre. The
// centre button (or A) shows every app instead. The ring follows the theme: accent outline, data face for the labels.
Item {
    id: m
    property bool showAll: false
    readonly property real footH: Style.space(48)
    readonly property int n: Launcher.menu.pinnedCount
    readonly property real radius: Math.min(width / 2 - Style.space(64), (height - footH) / 2 - Style.space(52))
    readonly property real cx: width / 2
    readonly property real cy: (height - footH) / 2
    onShowAllChanged: Launcher.menu.setLayout(showAll ? "ring-all" : "ring")
    Component.onCompleted: Launcher.menu.setLayout("ring")
    onVisibleChanged: if (visible) { showAll = false; Launcher.menu.setLayout("ring") }

    ActionsList { visible: Launcher.actionsMode; anchors.fill: parent; anchors.bottomMargin: m.footH }
    Item {
        visible: !Launcher.actionsMode && !m.showAll
        anchors.fill: parent
        anchors.bottomMargin: m.footH
        Component.onCompleted: Launcher.menu.setColumns("pinned", Math.max(1, m.n))
        Connections { target: m; function onNChanged() { Launcher.menu.setColumns("pinned", Math.max(1, m.n)) } }
        Rectangle {   // the ring itself
            x: m.cx - m.radius; y: m.cy - m.radius
            width: m.radius * 2; height: width; radius: width / 2
            color: "transparent"
            border.width: Math.max(1, Style.borderWidth)
            border.color: Style.accentDim
        }
        Repeater {
            model: Launcher.menu.pinned
            delegate: AppTile {
                required property var model
                readonly property int index: model.index
                appId: model.appId; name: model.name; icon: model.icon; hasIcon: model.hasIcon; group: model.group
                readonly property real ang: -Math.PI / 2 + index * 2 * Math.PI / Math.max(1, m.n)
                width: Style.space(80); height: Style.space(80)
                x: m.cx + m.radius * Math.cos(ang) - width / 2
                y: m.cy + m.radius * Math.sin(ang) - height / 2
                iconSize: Style.space(36)
                showGroup: false
                selected: Launcher.menu.zone === "pinned" && Launcher.menu.index === index
                onActivated: Launcher.menu.activate("pinned", index)
                onContextRequested: Launcher.menu.openContextFor("pinned", index)
                onHovered: Launcher.menu.hover("pinned", index)
            }
        }
        Column {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: -m.footH / 2 + (m.cy - parent.height / 2) + m.footH / 2
            x: m.cx - width / 2
            width: m.radius * 1.2
            spacing: Style.space(8)
            Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                   text: Launcher.menu.zone === "pinned" && Launcher.menu.index >= 0 ? Launcher.menu.currentName : qsTr("Pinned")
                   color: Style.text; font.family: Style.fontDisplayStack; font.pixelSize: Style.fontSize + 6; font.weight: Font.DemiBold; font.capitalization: Style.upperTitles ? Font.AllUppercase : Font.MixedCase }
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: allLab.implicitWidth + Style.space(28); height: Style.space(32); radius: Style.radius(16)
                color: "transparent"; border.width: Math.max(1, Style.borderWidth); border.color: Style.accent
                Accessible.role: Accessible.Button; Accessible.name: qsTr("All apps")
                Text { id: allLab; anchors.centerIn: parent; text: qsTr("All apps"); color: Style.accent; font.family: Style.fontDataStack; font.pixelSize: Style.fontSize; Accessible.ignored: true }
                MouseArea { anchors.fill: parent; onClicked: m.showAll = true; Accessible.ignored: true }
            }
        }
    }
    Item {
        visible: !Launcher.actionsMode && m.showAll
        anchors.fill: parent
        anchors.bottomMargin: m.footH
        AllApps { anchors.fill: parent; anchors.topMargin: Style.space(36) }
        Rectangle {
            x: Style.space(12); y: Style.space(8); height: Style.space(28); width: backLab.implicitWidth + Style.space(24); radius: Style.radius(14)
            color: "transparent"; border.width: Math.max(1, Style.borderWidth); border.color: Style.accent
            Accessible.role: Accessible.Button; Accessible.name: qsTr("Back to the ring")
            Text { id: backLab; anchors.centerIn: parent; text: "\u2039  " + qsTr("Ring"); color: Style.accent; font.family: Style.fontDataStack; font.pixelSize: Style.fontSize - 1; Accessible.ignored: true }
            MouseArea { anchors.fill: parent; onClicked: m.showAll = false; Accessible.ignored: true }
        }
    }
    MenuFooter { compact: true; y: parent.height - height; width: parent.width; height: m.footH }
}
