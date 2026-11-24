import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Style 1.0

// A 0 to 100 slider with its value in words. `moved2(v)` fires only when the person drags or uses the keys.
C.Slider {
    id: s
    property string label: ""
    signal changedByPerson(int value)
    from: 0; to: 100; stepSize: 1
    implicitWidth: 220
    implicitHeight: 28
    Accessible.name: label
    Accessible.role: Accessible.Slider
    onMoved: changedByPerson(Math.round(value))
    background: Rectangle {
        x: s.leftPadding; y: s.topPadding + s.availableHeight / 2 - height / 2
        width: s.availableWidth; height: 4; radius: 2
        color: TaosStyle.track
        Rectangle { width: s.visualPosition * parent.width; height: parent.height; radius: 2; color: TaosStyle.accent }
    }
    handle: Rectangle {
        x: s.leftPadding + s.visualPosition * (s.availableWidth - width)
        y: s.topPadding + s.availableHeight / 2 - height / 2
        width: 18; height: 18; radius: 9
        color: TaosStyle.accent
        border.width: s.visualFocus ? 3 : 0
        border.color: TaosStyle.focusRing
    }
}
