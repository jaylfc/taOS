// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// One app as a list row: icon, name, the app group on the right in a quiet tone. `plain` drops the icon (the text style).
Item {
    id: r
    property string appId: ""
    property string name: ""
    property string icon: ""
    property bool hasIcon: false
    property string group: ""
    property bool selected: false
    property bool plain: false
    property real iconSize: Style.space(28)
    signal activated()
    signal contextRequested()
    signal hovered()
    height: plain ? Style.space(44) : Style.space(40)

    Accessible.role: Accessible.ListItem
    Accessible.name: group.length > 0 ? qsTr("%1, in %2").arg(name).arg(group) : name
    Accessible.selected: selected
    Accessible.focusable: true
    Accessible.onPressAction: r.activated()

    Rectangle {
        anchors.fill: parent
        anchors.leftMargin: Style.space(4)
        anchors.rightMargin: Style.space(4)
        radius: Style.radius(10)
        color: r.selected ? Style.raised : area.containsMouse ? Style.surface : "transparent"
        border.width: r.selected ? Math.max(1, Style.borderWidth) : 0
        border.color: Style.focusRing
        Behavior on color { ColorAnimation { duration: Style.reduceMotion ? 0 : 120 } }
    }
    AppIcon {
        id: ic
        visible: !r.plain
        x: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        size: r.iconSize; source: r.icon; hasIcon: r.hasIcon; name: r.name
    }
    Text {
        anchors.left: r.plain ? parent.left : ic.right
        anchors.leftMargin: r.plain ? Style.space(12) : Style.space(12)
        anchors.right: grp.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        text: r.name
        color: Style.text
        elide: Text.ElideRight
        font.family: r.plain ? Style.fontDisplayStack : Style.fontStack
        font.pixelSize: r.plain ? Style.fontSize + 8 : Style.fontSize + 1
        font.weight: r.plain ? Font.Normal : Font.Medium
    }
    Text {
        id: grp
        anchors.right: parent.right
        anchors.rightMargin: Style.space(16)
        anchors.verticalCenter: parent.verticalCenter
        width: visible ? implicitWidth : 0
        visible: r.group.length > 0
        text: r.group
        color: Style.textTertiary
        font.family: Style.fontStack
        font.pixelSize: Style.fontSize - 2
    }
    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onEntered: r.hovered()
        onClicked: (m) => { if (m.button === Qt.RightButton) r.contextRequested(); else r.activated(); }
        onPressAndHold: r.contextRequested()
        Accessible.ignored: true
    }
}
