import QtQuick
import Taos.Bar

// taOS status bar, version 0. Left: taOS button and the (empty, reserved)
// focused-window slot. Right: network, volume, battery, clock and date.
Item {
    id: root
    property int barHeight: 40
    property string outputName: ""
    readonly property int sidePadding: 12
    readonly property int itemSpacing: 4

    // Filled later by the window tracker: focused app name and its group chip.
    property string focusedAppName: ""
    property string focusedGroup: ""

    implicitWidth: 1920
    implicitHeight: barHeight
    height: barHeight

    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    Accessible.role: Accessible.ToolBar
    Accessible.name: qsTr("taOS status bar")

    Rectangle { anchors.fill: parent; color: Theme.barBackground }
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.stroke
    }

    // Left: taOS button, then the reserved slot (fixed width so nothing jumps when it fills).
    Row {
        id: leftRow
        anchors.left: parent.left
        anchors.leftMargin: root.sidePadding
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 1
        spacing: root.itemSpacing

        BarItem {
            id: taosButton
            interactive: true
            accessibleName: qsTr("taOS menu")
            onActivated: Launcher.launch()
            Text {
                text: "taOS"
                color: Theme.accent
                font.family: root.fontFamily
                font.pixelSize: 13
                font.weight: Font.Bold
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        Item {
            id: windowSlot
            objectName: "windowSlot"
            height: parent.height
            width: Math.max(120, Math.min(360, root.width * 0.28))
            clip: true
            Accessible.role: Accessible.StaticText
            Accessible.name: text.text
            Text {
                id: text
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width
                elide: Text.ElideRight
                text: root.focusedAppName
                color: Theme.text
                font.family: root.fontFamily
                font.pixelSize: 13
                font.weight: Font.Medium
                renderType: Text.NativeRendering
            }
            // Group chip goes here later (app-model section 10); nothing is drawn while empty.
        }
    }

    // Right: status items, then clock and date.
    Row {
        id: rightRow
        anchors.right: parent.right
        anchors.rightMargin: root.sidePadding
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 1
        spacing: root.itemSpacing

        BarItem {
            accessibleName: Network.description
            NetworkIcon { kind: Network.kind; anchors.verticalCenter: parent.verticalCenter }
        }
        BarItem {
            visible: Volume.available
            interactive: true
            accessibleName: Volume.description
            onActivated: Volume.refresh()
            VolumeIcon { muted: Volume.muted; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: Volume.muted ? qsTr("Muted") : Volume.percent + "%"
                color: Theme.textSecondary
                font.family: root.fontFamily
                font.pixelSize: 13
                font.weight: Font.Medium
                font.features: { "tnum": 1 }
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        BarItem {
            visible: Battery.present
            accessibleName: Battery.description
            BatteryIcon { percent: Battery.percent; charging: Battery.charging; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: Battery.percent + "%"
                color: Theme.textSecondary
                font.family: root.fontFamily
                font.pixelSize: 13
                font.weight: Font.Medium
                font.features: { "tnum": 1 }
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        BarItem {
            accessibleName: qsTr("Date and time: %1 %2").arg(Clock.date).arg(Clock.time)
            spacing: 8
            Text {
                text: Clock.date
                color: Theme.textSecondary
                font.family: root.fontFamily
                font.pixelSize: 13
                font.weight: Font.Medium
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: Clock.time
                color: Theme.text
                font.family: root.fontFamily
                font.pixelSize: 13
                font.weight: Font.Medium
                font.features: { "tnum": 1 }
                renderType: Text.NativeRendering
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
