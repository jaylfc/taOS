// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// A placeholder block in the shape of what is loading. Pulses gently; still when reduced motion is asked for.
Rectangle {
    id: root
    radius: Look.radiusControl
    color: Look.tint(Look.text, 0.07)
    Accessible.ignored: true
    SequentialAnimation on opacity {
        running: !Look.reduceMotion && root.visible
        loops: Animation.Infinite
        NumberAnimation { from: 1.0; to: 0.55; duration: 900; easing.type: Easing.InOutSine }
        NumberAnimation { from: 0.55; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
    }
}
