// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// A thin progress bar. `value` runs 0 to 1; `tone` is accent | success | warning | danger. `indeterminate` shows a
// moving block (still when the person asked for reduced motion).
Item {
    id: root
    property real value: 0
    property string tone: "accent"
    property bool indeterminate: false
    implicitHeight: 6
    implicitWidth: 120
    Accessible.role: Accessible.ProgressBar
    Accessible.name: indeterminate ? qsTr("Working") : qsTr("%1 percent").arg(Math.round(value * 100))
    Rectangle { anchors.fill: parent; radius: height / 2; color: Look.tint(Look.text, 0.10) }
    Rectangle {
        id: fill
        visible: !root.indeterminate
        width: Math.max(root.value > 0 ? height : 0, parent.width * Math.max(0, Math.min(1, root.value)))
        height: parent.height
        radius: height / 2
        color: root.tone === "accent" ? Look.accent : Look.tone(root.tone)
        Behavior on width { NumberAnimation { duration: Look.base; easing.type: Look.ease } }
    }
    Item {
        anchors.fill: parent
        visible: root.indeterminate
        clip: true
        Rectangle {
            id: block
            width: parent.width * 0.3; height: parent.height; radius: height / 2
            color: Look.accent
            x: Look.reduceMotion ? parent.width * 0.35 : -width
            SequentialAnimation on x {
                running: root.indeterminate && root.visible && !Look.reduceMotion
                loops: Animation.Infinite
                NumberAnimation { from: -block.width; to: root.width; duration: 1100; easing.type: Easing.InOutSine }
            }
        }
    }
}
