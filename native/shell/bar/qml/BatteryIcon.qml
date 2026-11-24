// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Bar

// Battery outline with a level fill; accent when charging, warning at 20%, danger at 10%.
Item {
    id: icon
    property int percent: 100
    property bool charging: false
    width: 22
    height: 16
    readonly property color level: charging ? Theme.accent
                                 : percent <= 10 ? Theme.danger
                                 : percent <= 20 ? Theme.warning : Theme.text

    Rectangle {
        id: body
        x: 0; y: 3; width: 19; height: 10; radius: 2.5
        color: "transparent"
        border.width: 1.5
        border.color: icon.level
        Rectangle {
            x: 2.5; y: 2.5
            height: parent.height - 5
            width: Math.max(1, (parent.width - 5) * Math.max(0, Math.min(100, icon.percent)) / 100)
            radius: 1
            color: icon.level
        }
    }
    Rectangle { x: 19.5; y: 6; width: 2; height: 4; radius: 1; color: icon.level }
}
