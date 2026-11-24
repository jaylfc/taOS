// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// The strip form of the top bar (layout.barStyle "strip"): a thin instrument strip. Left: the taOS mark, workspace
// ticks. Right: the network, the volume as a ten tick meter, the clock. Labels are small, upper case and letter
// spaced in the secondary colour; values are in the text colour and the data face.
Item {
    id: strip
    property string outputName: ""
    property int insetTop: 0
    property int insetBottom: 0
    readonly property int labelSize: Math.max(9, Theme.fontSize - 2)
    readonly property real tracking: Theme.titleTracking * labelSize
    readonly property int pad: Math.round((14) * Theme.spacingFactor)
    Accessible.role: Accessible.ToolBar
    Accessible.name: qsTr("taOS status strip")

    component Label: Text {
        color: Theme.textSecondary
        font.family: Theme.fontDataStack
        font.pixelSize: strip.labelSize
        font.capitalization: Theme.upperTitles ? Font.AllUppercase : Font.MixedCase
        font.letterSpacing: strip.tracking
        renderType: Text.NativeRendering
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
    }

    Row {
        id: left
        anchors.left: parent.left
        anchors.leftMargin: strip.pad
        anchors.top: parent.top
        anchors.topMargin: strip.insetTop
        anchors.bottom: parent.bottom
        anchors.bottomMargin: strip.insetBottom
        spacing: Math.round((18) * Theme.spacingFactor)
        BarItem {
            id: mark
            interactive: true
            hPad: 0
            accessibleName: qsTr("taOS menu")
            onActivated: Launcher.launch()
            Text {
                text: "taOS"
                color: Theme.accent
                font.family: Theme.fontDisplayStack
                font.pixelSize: Theme.fontSize + 1
                font.weight: Font.DemiBold
                font.letterSpacing: Theme.titleTracking * Theme.fontSize
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        Item {
            id: ws
            height: parent.height
            width: wsRow.width
            Accessible.role: Accessible.StaticText
            Accessible.name: qsTr("Workspaces")
            Row {
                id: wsRow
                anchors.verticalCenter: parent.verticalCenter
                spacing: Math.round((8) * Theme.spacingFactor)
                Label { text: qsTr("Space") }
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 5
                    Repeater {
                        model: Workspaces.list.length > 0 ? Workspaces.list : [{ name: "1", focused: true }]
                        Rectangle {
                            required property var modelData
                            width: 14
                            height: 5
                            color: modelData.focused ? Theme.accent : "transparent"
                            border.width: 1
                            border.color: modelData.focused ? Theme.accent : Theme.accentDim
                            anchors.verticalCenter: parent.verticalCenter
                            Accessible.ignored: true
                        }
                    }
                }
            }
        }
    }

    Row {
        id: right
        anchors.right: parent.right
        anchors.rightMargin: strip.pad
        anchors.top: parent.top
        anchors.topMargin: strip.insetTop
        anchors.bottom: parent.bottom
        anchors.bottomMargin: strip.insetBottom
        spacing: Math.round((10) * Theme.spacingFactor)
        BarItem {
            id: displayItem
            visible: Display.outputs.length > 0
            interactive: true
            hPad: 4
            accessibleName: qsTr("Display and appearance")
            accessibleHint: qsTr("Opens the display menu: scale, refresh rate, dark or light, and the colour theme")
            onActivated: MenuHost.openDisplay(strip.outputName, displayItem.mapToItem(null, displayItem.width, 0).x)
            MonitorGlyph { anchors.verticalCenter: parent.verticalCenter }
        }
        BarItem {
            id: netItem
            interactive: true
            accessibleName: Network.description
            accessibleHint: qsTr("Opens the Wi-Fi, Ethernet and Bluetooth menu")
            onActivated: MenuHost.openNetwork(strip.outputName, netItem.mapToItem(null, netItem.width, 0).x)
            Label { text: qsTr("Net") }
            NetworkIcon { kind: Network.kind; strength: Network.strength; anchors.verticalCenter: parent.verticalCenter }
        }
        BarItem {
            id: volItem
            interactive: true
            accessibleName: Audio.description
            accessibleHint: qsTr("Opens the sound menu")
            onActivated: MenuHost.openAudio(strip.outputName, volItem.mapToItem(null, volItem.width, 0).x)
            Label { text: qsTr("Vol") }
            TickMeter { anchors.verticalCenter: parent.verticalCenter; value: Audio.hasOutput ? Audio.percent / 100 : 0; dim: Audio.muted || !Audio.hasOutput }
        }
        BarItem {
            accessibleName: qsTr("Date and time: %1 %2").arg(Clock.date).arg(Clock.time)
            Text {
                text: Clock.time
                color: Theme.text
                font.family: Theme.fontDataStack
                font.pixelSize: Theme.fontSize
                font.weight: Font.Medium
                font.features: { "tnum": 1 }
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
