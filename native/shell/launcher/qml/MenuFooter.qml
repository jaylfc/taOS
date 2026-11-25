// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// The person, Settings, Files, the action menu and power. The power list opens upward and every entry that ends the
// session asks for a second Enter (or a second click).
Item {
    id: f
    property bool compact: false
    implicitHeight: Style.space(compact ? 48 : 56)
    Rectangle { width: parent.width; height: Math.max(1, Style.borderWidth); color: Style.hairline }

    Row {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(16)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(12)
        Rectangle {
            width: Style.space(32); height: width; radius: width / 2
            color: Style.raised
            border.width: Math.max(1, Style.borderWidth); border.color: Style.hairline
            clip: true
            Image { anchors.fill: parent; visible: Launcher.menu.userAvatar.length > 0; source: Launcher.menu.userAvatar; fillMode: Image.PreserveAspectCrop; sourceSize: Qt.size(64, 64) }
            Text { anchors.centerIn: parent; visible: Launcher.menu.userAvatar.length === 0; text: Launcher.menu.userName.length ? Launcher.menu.userName.charAt(0).toUpperCase() : "?"
                   color: Style.textSecondary; font.family: Style.fontDisplayStack; font.pixelSize: Style.fontSize + 2; font.weight: Font.DemiBold }
            Accessible.ignored: true
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: !f.compact
            text: Launcher.menu.userName
            color: Style.text
            font.family: Style.fontStack
            font.pixelSize: Style.fontSize + 1
            font.weight: Font.Medium
            Accessible.name: qsTr("Signed in as %1").arg(Launcher.menu.userName)
        }
    }
    Row {
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(4)
        Repeater {
            model: Launcher.menu.footerIds
            delegate: Rectangle {
                id: btn
                required property int index
                required property string modelData
                readonly property bool cur: Launcher.menu.zone === "footer" && Launcher.menu.index === index
                readonly property string label: Launcher.menu.footerLabels[index]
                width: lab.implicitWidth + Style.space(20)
                height: Style.space(32)
                radius: Style.radius(10)
                color: cur ? Style.raised : ma.containsMouse ? Style.surface : "transparent"
                border.width: cur ? Math.max(1, Style.borderWidth) : 0
                border.color: Style.focusRing
                Accessible.role: Accessible.Button
                Accessible.name: label
                Accessible.onPressAction: Launcher.menu.runFooter(index)
                Text { id: lab; anchors.centerIn: parent; text: btn.label; color: btn.modelData === "power" ? Style.text : Style.textSecondary
                       font.family: Style.fontStack; font.pixelSize: Style.fontSize; font.weight: Font.Medium; Accessible.ignored: true }
                MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; onEntered: Launcher.menu.hover("footer", btn.index); onClicked: Launcher.menu.runFooter(btn.index); Accessible.ignored: true }
            }
        }
    }

    // the power list
    Rectangle {
        id: pop
        visible: Launcher.menu.powerOpen
        z: 5
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.bottom: parent.top
        anchors.bottomMargin: Style.space(8)
        width: Style.space(220)
        height: list.count * Style.space(40) + Style.space(16)
        radius: Style.radius(14)
        color: Style.ground
        border.width: Math.max(1, Style.borderWidth)
        border.color: Style.glassStroke
        Accessible.role: Accessible.PopupMenu
        Accessible.name: qsTr("Power")
        Column {
            id: list
            readonly property int count: rep.count
            anchors.fill: parent
            anchors.margins: Style.space(8)
            Repeater {
                model: Launcher.menu.power
                delegate: Rectangle {
                    required property int index
                    required property string title
                    required property string actionId
                    readonly property bool cur: Launcher.menu.powerIndex === index
                    readonly property bool asking: Launcher.menu.confirmId === actionId
                    width: list.width
                    height: Style.space(40)
                    radius: Style.radius(10)
                    color: cur ? Style.raised : "transparent"
                    border.width: asking ? Math.max(1, Style.borderWidth) : 0
                    border.color: Style.danger
                    Accessible.role: Accessible.MenuItem
                    Accessible.name: asking ? qsTr("%1, press Enter again to confirm").arg(title) : title
                    Text { anchors.left: parent.left; anchors.leftMargin: Style.space(12); anchors.verticalCenter: parent.verticalCenter
                           text: parent.asking ? qsTr("%1: confirm").arg(parent.title) : parent.title
                           color: parent.asking ? Style.danger : Style.text; font.family: Style.fontStack; font.pixelSize: Style.fontSize + 1; Accessible.ignored: true }
                    MouseArea { anchors.fill: parent; hoverEnabled: true; onClicked: Launcher.menu.runPower(index); Accessible.ignored: true }
                }
            }
        }
    }
}
