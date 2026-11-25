// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// One app as a tile: icon over name, the app group (if any) quietly under it. Click or Enter opens, right click or
// long press (or the Menu key) shows what else can be done.
Item {
    id: t
    property string appId: ""
    property string name: ""
    property string icon: ""
    property bool hasIcon: false
    property string group: ""
    property bool selected: false
    property real iconSize: Style.space(40)
    property bool showGroup: true
    signal activated()
    signal contextRequested()
    signal hovered()

    Accessible.role: Accessible.Button
    Accessible.name: group.length > 0 ? qsTr("%1, in %2").arg(name).arg(group) : name
    Accessible.focusable: true
    Accessible.onPressAction: t.activated()

    Rectangle {
        anchors.fill: parent
        anchors.margins: Style.space(2)
        radius: Style.radius(12)
        color: t.selected ? Style.raised : area.containsMouse ? Style.surface : "transparent"
        border.width: t.selected ? Math.max(1, Style.borderWidth) : 0
        border.color: Style.focusRing
        Behavior on color { ColorAnimation { duration: Style.reduceMotion ? 0 : 120 } }
    }
    Column {
        anchors.centerIn: parent
        width: parent.width - Style.space(8)
        spacing: Style.space(4)
        AppIcon { anchors.horizontalCenter: parent.horizontalCenter; size: t.iconSize; source: t.icon; hasIcon: t.hasIcon; name: t.name }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: t.name
            color: Style.text
            elide: Text.ElideRight
            maximumLineCount: 1
            font.family: Style.fontStack
            font.pixelSize: Style.fontSize
            font.weight: Font.Medium
        }
        Text {
            width: parent.width
            visible: t.showGroup && t.group.length > 0
            horizontalAlignment: Text.AlignHCenter
            text: t.group
            color: Style.textTertiary
            elide: Text.ElideRight
            font.family: Style.fontStack
            font.pixelSize: Style.fontSize - 3
        }
    }
    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onEntered: t.hovered()
        onClicked: (m) => { if (m.button === Qt.RightButton) t.contextRequested(); else t.activated(); }
        onPressAndHold: t.contextRequested()
        Accessible.ignored: true
    }
}
