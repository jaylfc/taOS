// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Style 1.0

// A switch. `label` is its accessible name; `toggled(bool)` fires only when the person changes it.
C.Switch {
    id: sw
    property string label: ""
    signal flipped(bool on)
    implicitWidth: 44
    implicitHeight: 28
    padding: 0
    Accessible.name: label
    Accessible.role: Accessible.CheckBox
    onToggled: flipped(checked)
    indicator: Rectangle {
        implicitWidth: 44; implicitHeight: 26
        x: 0; y: (sw.height - height) / 2
        radius: height / 2
        color: sw.checked ? TaosStyle.accent : TaosStyle.track
        border.width: sw.visualFocus ? 2 : 1
        border.color: sw.visualFocus ? TaosStyle.focusRing : TaosStyle.hairline
        Behavior on color { ColorAnimation { duration: TaosStyle.reduceMotion ? 0 : 180 } }
        Rectangle {
            y: 3; width: 20; height: 20; radius: 10
            x: sw.checked ? parent.width - width - 3 : 3
            color: sw.checked ? TaosStyle.accentText : TaosStyle.textSecondary
            Behavior on x { NumberAnimation { duration: TaosStyle.reduceMotion ? 0 : 180; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: Item {}
}
