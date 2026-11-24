// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Window
import Taos.Bar

// The tray of a taskbar layout: display, network, sound and the clock at the far end of the taskbar. taos-dock draws
// the taskbar to the left of this surface; both take their size from the Theme, so they tile without talking.
Item {
    id: root
    property string outputName: ""
    readonly property int itemW: Theme.taskbarHeight - 4
    readonly property string fontFamily: Theme.fontStack
    readonly property bool atBottom: Theme.dockEdge === "bottom"
    // Where the menus put their right edge, in output coordinates (this surface hugs the right edge of the output).
    function menuX(item) { return Screen.width - root.width + item.mapToItem(null, item.width, 0).x }
    implicitWidth: Theme.trayWidth
    implicitHeight: Theme.taskbarHeight
    Accessible.role: Accessible.ToolBar
    Accessible.name: qsTr("System tray")

    Surface { id: surf; anchors.fill: parent; kind: Theme.dockSurface; lineSide: root.atBottom ? "top" : "bottom" }
    Row {
        anchors.right: parent.right
        anchors.rightMargin: Math.round((10) * Theme.spacingFactor)
        anchors.top: parent.top
        anchors.topMargin: surf.insetTop
        anchors.bottom: parent.bottom
        anchors.bottomMargin: surf.insetBottom
        spacing: 0
        BarItem {
            id: displayItem
            visible: Display.outputs.length > 0
            width: root.itemW
            hPad: 0
            interactive: true
            accessibleName: qsTr("Display and appearance")
            accessibleHint: qsTr("Opens the display menu: scale, refresh rate, dark or light, and the colour theme")
            onActivated: MenuHost.openDisplay(root.outputName, root.menuX(displayItem))
            MonitorGlyph { anchors.verticalCenter: parent.verticalCenter }
        }
        BarItem {
            id: networkItem
            width: root.itemW
            hPad: 0
            interactive: true
            accessibleName: Network.description
            accessibleHint: qsTr("Opens the Wi-Fi, Ethernet and Bluetooth menu")
            onActivated: MenuHost.openNetwork(root.outputName, root.menuX(networkItem))
            NetworkIcon { kind: Network.kind; strength: Network.strength; anchors.verticalCenter: parent.verticalCenter }
        }
        BarItem {
            id: audioItem
            width: root.itemW
            hPad: 0
            interactive: true
            accessibleName: Audio.description
            accessibleHint: qsTr("Opens the sound menu")
            onActivated: MenuHost.openAudio(root.outputName, root.menuX(audioItem))
            VolumeIcon { level: Audio.level; anchors.verticalCenter: parent.verticalCenter }
        }
        BarItem {
            width: Math.round(Theme.fontSize * 3.6) + Math.round((20) * Theme.spacingFactor)
            hPad: 0
            accessibleName: qsTr("Date and time: %1 %2").arg(Clock.date).arg(Clock.time)
            Text {
                text: Clock.time
                color: Theme.text
                font.family: root.fontFamily
                font.pixelSize: Theme.fontSize
                font.weight: Font.Medium
                font.features: { "tnum": 1 }
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
