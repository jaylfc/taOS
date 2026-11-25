// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// What else can be done with an app (right click, long press, Menu key, Tab), and its info. A sheet over the menu: Up and
// Down choose, Enter does it, Escape closes. Uninstall asks twice.
Item {
    id: cs
    readonly property bool shown: Launcher.menu.contextOpen || Launcher.menu.infoOpen
    visible: shown
    anchors.fill: parent
    z: 20
    Rectangle { anchors.fill: parent; color: Style.scrim; opacity: 0.55 }
    MouseArea { anchors.fill: parent; onClicked: { Launcher.menu.back() } Accessible.ignored: true }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(Style.space(300), cs.width - Style.space(32))
        height: (Launcher.menu.infoOpen ? infoCol.implicitHeight : actCol.implicitHeight) + Style.space(24)
        radius: Style.radius(16)
        color: Style.ground
        border.width: Math.max(1, Style.borderWidth)
        border.color: Style.glassStroke
        Accessible.role: Accessible.PopupMenu
        Accessible.name: Launcher.menu.contextTitle
        MouseArea { anchors.fill: parent; Accessible.ignored: true }

        Column {
            id: actCol
            visible: Launcher.menu.contextOpen
            x: Style.space(8); y: Style.space(12)
            width: parent.width - Style.space(16)
            spacing: Style.space(2)
            SectionLabel { leftPadding: Style.space(8); bottomPadding: Style.space(4); text: Launcher.menu.contextTitle; width: parent.width; elide: Text.ElideRight }
            Repeater {
                model: Launcher.menu.contextLabels
                delegate: Rectangle {
                    required property int index
                    required property string modelData
                    readonly property bool danger: Launcher.menu.contextDanger[index] === "1"
                    readonly property bool cur: Launcher.menu.contextIndex === index
                    readonly property bool asking: danger && Launcher.menu.confirmId.length > 0 && cur
                    width: actCol.width
                    height: Style.space(40)
                    radius: Style.radius(10)
                    color: cur ? Style.raised : "transparent"
                    border.width: asking ? Math.max(1, Style.borderWidth) : 0
                    border.color: Style.danger
                    Accessible.role: Accessible.MenuItem
                    Accessible.name: asking ? qsTr("%1, press Enter again to confirm").arg(modelData) : modelData
                    Text { anchors.left: parent.left; anchors.leftMargin: Style.space(12); anchors.verticalCenter: parent.verticalCenter
                           text: parent.asking ? qsTr("%1: press Enter again").arg(parent.modelData) : parent.modelData
                           color: parent.danger ? Style.danger : Style.text; font.family: Style.fontStack; font.pixelSize: Style.fontSize + 1; Accessible.ignored: true }
                    MouseArea { anchors.fill: parent; hoverEnabled: true; onEntered: Launcher.menu.setContextIndex(index); onClicked: Launcher.menu.runContext(index); Accessible.ignored: true }
                }
            }
        }
        Column {
            id: infoCol
            visible: Launcher.menu.infoOpen
            x: Style.space(16); y: Style.space(16)
            width: parent.width - Style.space(32)
            spacing: Style.space(8)
            Row {
                spacing: Style.space(12)
                AppIcon { size: Style.space(48); source: Launcher.menu.info.icon || ""; hasIcon: (Launcher.menu.info.icon || "").length > 0; name: Launcher.menu.info.name || "" }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    Text { text: Launcher.menu.info.name || ""; color: Style.text; font.family: Style.fontDisplayStack; font.pixelSize: Style.fontSize + 4; font.weight: Font.DemiBold }
                    Text { text: Launcher.menu.info.description || ""; color: Style.textSecondary; font.family: Style.fontStack; font.pixelSize: Style.fontSize - 1
                           width: infoCol.width - Style.space(64); elide: Text.ElideRight }
                }
            }
            Repeater {
                model: [ [qsTr("Kind"), Launcher.menu.info.kind], [qsTr("Category"), Launcher.menu.info.category], [qsTr("App group"), Launcher.menu.info.group],
                         [qsTr("Command"), Launcher.menu.info.command], [qsTr("Id"), Launcher.menu.info.id] ]
                delegate: Row {
                    required property var modelData
                    visible: (modelData[1] || "").length > 0
                    spacing: Style.space(12)
                    Text { width: Style.space(72); text: parent.modelData[0]; color: Style.textTertiary; font.family: Style.fontStack; font.pixelSize: Style.fontSize - 1 }
                    Text { width: infoCol.width - Style.space(84); text: parent.modelData[1] || ""; color: Style.text; elide: Text.ElideMiddle; font.family: Style.fontDataStack; font.pixelSize: Style.fontSize - 1 }
                }
            }
            Text { text: qsTr("Press Escape to close"); color: Style.textTertiary; font.family: Style.fontStack; font.pixelSize: Style.fontSize - 2 }
        }
    }
}
