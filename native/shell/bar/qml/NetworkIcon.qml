// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// Wired, Wi-Fi and offline glyphs drawn from rectangles (no icon assets).
Item {
    id: icon
    property string kind: "offline"
    property int strength: 0   // Wi-Fi signal 0..100; 0 means unknown, all bars lit
    readonly property int lit: strength <= 0 ? 4 : Math.max(1, Math.min(4, Math.ceil(strength / 25)))
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
                opacity: icon.kind === "wifi" && index >= icon.lit ? 0.3 : 1
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
            border.width: Theme.iconStroke
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
