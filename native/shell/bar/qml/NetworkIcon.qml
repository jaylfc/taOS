import QtQuick
import Taos.Bar

// Wired, Wi-Fi and offline glyphs drawn from rectangles (no icon assets).
Item {
    id: icon
    property string kind: "offline"
    property color color: Theme.text
    width: 18
    height: 16

    // Wi-Fi: four ascending bars. Offline: the same bars, dimmed, struck through.
    Row {
        visible: icon.kind !== "wired"
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 1
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 2
        Repeater {
            model: 4
            Rectangle {
                width: 3
                height: 4 + index * 3
                anchors.bottom: parent.bottom
                radius: 1
                color: icon.kind === "wifi" ? icon.color : Theme.textTertiary
            }
        }
    }
    Rectangle {
        visible: icon.kind === "offline"
        anchors.centerIn: parent
        width: 20
        height: 1.5
        radius: 1
        rotation: -40
        color: Theme.danger
    }
    // Wired: an RJ45 style plug.
    Item {
        visible: icon.kind === "wired"
        anchors.centerIn: parent
        width: 14
        height: 12
        Rectangle {
            anchors.top: parent.top
            anchors.horizontalCenter: parent.horizontalCenter
            width: 14
            height: 9
            radius: 2
            color: "transparent"
            border.width: 1.5
            border.color: icon.color
        }
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            width: 6
            height: 3
            radius: 1
            color: icon.color
        }
        Row {
            anchors.top: parent.top
            anchors.topMargin: 2.5
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 1.5
            Repeater {
                model: 4
                Rectangle { width: 1.5; height: 3; color: icon.color }
            }
        }
    }
}
