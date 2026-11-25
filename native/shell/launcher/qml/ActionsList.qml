// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// The system action menu, one level at a time: type to filter, Enter to open a group or run an action, Escape or
// Backspace on an empty filter to go up. Power style actions ask for a second Enter.
Item {
    id: al
    property bool plain: false
    function ensureVisible() { list.positionViewAtIndex(Launcher.menu.actionIndex, ListView.Contain) }
    Connections { target: Launcher.menu; function onCursorChanged() { al.ensureVisible() } }

    Row {
        id: crumbs
        x: Style.space(16)
        height: Style.space(40)
        spacing: Style.space(8)
        SectionLabel { anchors.verticalCenter: parent.verticalCenter; text: qsTr("Actions") }
        Repeater {
            model: Launcher.menu.actionPath.length > 0 ? 1 : 0
            delegate: Row {
                spacing: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                Text { text: "\u203A"; color: Style.textTertiary; font.family: Style.fontStack; font.pixelSize: Style.fontSize }
                SectionLabel { color: Style.text; text: Launcher.menu.actionTitle }
            }
        }
    }
    Text {
        anchors.right: parent.right
        anchors.rightMargin: Style.space(16)
        y: Style.space(12)
        text: Launcher.menu.actionPath.length > 0 ? qsTr("Esc: back") : qsTr("Type to filter")
        color: Style.textTertiary
        font.family: Style.fontStack
        font.pixelSize: Style.fontSize - 2
    }
    ListView {
        id: list
        y: crumbs.height
        width: parent.width
        height: parent.height - crumbs.height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: Launcher.menu.actions
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Actions")
        delegate: Item {
            id: ad
            required property int index
            required property string actionId
            required property string title
            required property string subtitle
            required property string icon
            required property bool hasIcon
            required property bool isGroup
            required property bool confirm
            readonly property bool cur: Launcher.menu.actionIndex === index
            readonly property bool asking: Launcher.menu.confirmId === actionId
            width: ListView.view.width
            height: Style.space(48)
            Accessible.role: Accessible.ListItem
            Accessible.name: isGroup ? qsTr("%1, opens a list").arg(title) : asking ? qsTr("%1, press Enter again to confirm").arg(title) : title
            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: Style.space(4); anchors.rightMargin: Style.space(4)
                radius: Style.radius(10)
                color: ad.cur ? Style.raised : "transparent"
                border.width: ad.asking || ad.cur ? Math.max(1, Style.borderWidth) : 0
                border.color: ad.asking ? Style.danger : Style.focusRing
            }
            AppIcon { id: aic; visible: !al.plain; x: Style.space(12); anchors.verticalCenter: parent.verticalCenter; size: Style.space(28); source: ad.icon; hasIcon: ad.hasIcon; name: ad.title }
            Column {
                anchors.left: al.plain ? parent.left : aic.right
                anchors.leftMargin: Style.space(12)
                anchors.right: chev.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                Text { width: parent.width; text: ad.asking ? qsTr("%1: press Enter again to confirm").arg(ad.title) : ad.title; elide: Text.ElideRight
                       color: ad.asking ? Style.danger : Style.text; font.family: Style.fontStack; font.pixelSize: Style.fontSize + 1; font.weight: Font.Medium }
                Text { width: parent.width; visible: text.length > 0 && !al.plain; text: ad.subtitle; elide: Text.ElideRight; color: Style.textSecondary; font.family: Style.fontStack; font.pixelSize: Style.fontSize - 2 }
            }
            Text { id: chev; anchors.right: parent.right; anchors.rightMargin: Style.space(16); anchors.verticalCenter: parent.verticalCenter
                   text: ad.isGroup ? "\u203A" : ""; color: Style.textTertiary; font.family: Style.fontStack; font.pixelSize: Style.fontSize + 6 }
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                onEntered: Launcher.menu.setActionIndex(ad.index)
                onClicked: { Launcher.menu.setActionIndex(ad.index); Launcher.menuEnter() }
                Accessible.ignored: true
            }
        }
    }
    Text {
        anchors.centerIn: parent
        visible: list.count === 0
        text: qsTr("Nothing matches")
        color: Style.textSecondary
        font.family: Style.fontStack
        font.pixelSize: Style.fontSize + 1
    }
}
