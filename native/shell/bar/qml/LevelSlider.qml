import QtQuick
import Taos.Bar

// A volume slider: a thin track, a fill and a round knob. Arrow keys move it
// by 5, Home and End go to the ends. Announced as a slider with its value.
Item {
    id: sl
    property int value: 0           // 0..100, what the device has
    property string label: ""
    property bool dimmed: false     // muted: the fill is grey
    property real dragValue: 0
    readonly property bool dragging: drag.active || area.pressed
    readonly property real shown: dragging ? dragValue : value
    signal moved(int value)
    signal released()
    height: 24
    activeFocusOnTab: true

    Accessible.role: Accessible.Slider
    Accessible.name: label
    Accessible.description: qsTr("%1 percent").arg(Math.round(sl.shown))
    Accessible.focusable: true
    Accessible.onIncreaseAction: sl.nudge(5)
    Accessible.onDecreaseAction: sl.nudge(-5)

    function nudge(d) { var v = Math.max(0, Math.min(100, Math.round(sl.value + d))); sl.moved(v); sl.released() }
    function posToValue(x) { return Math.max(0, Math.min(100, Math.round(100 * (x - knob.width / 2) / Math.max(1, sl.width - knob.width)))) }

    Rectangle {
        id: track
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left; anchors.right: parent.right
        height: 4; radius: 2
        color: Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.22)
        Rectangle {
            height: parent.height; radius: 2
            width: knob.x + knob.width / 2
            color: sl.dimmed ? Theme.textTertiary : Theme.text
        }
    }
    Rectangle {
        id: knob
        width: 16; height: 16; radius: 8
        anchors.verticalCenter: parent.verticalCenter
        x: (sl.width - width) * Math.max(0, Math.min(100, sl.shown)) / 100
        color: "#FFFFFF"
        border.width: 0.5
        border.color: Qt.rgba(0, 0, 0, 0.25)
        Rectangle {
            anchors.fill: parent; anchors.margins: -3; radius: width / 2
            color: "transparent"; border.width: 1.5; border.color: Theme.text
            opacity: sl.activeFocus ? 0.45 : 0
        }
    }
    MouseArea {
        id: area
        anchors.fill: parent
        onPressed: function (m) { sl.dragValue = sl.posToValue(m.x); sl.moved(sl.dragValue) }
        onPositionChanged: function (m) { if (pressed) { sl.dragValue = sl.posToValue(m.x); sl.moved(sl.dragValue) } }
        onReleased: sl.released()
    }
    DragHandler { id: drag; enabled: false }
    Keys.onLeftPressed: nudge(-5)
    Keys.onRightPressed: nudge(5)
    Keys.onPressed: function (e) {
        if (e.key === Qt.Key_Home) { sl.moved(0); sl.released(); e.accepted = true }
        else if (e.key === Qt.Key_End) { sl.moved(100); sl.released(); e.accepted = true }
        else if (e.key === Qt.Key_PageUp) { nudge(10); e.accepted = true }
        else if (e.key === Qt.Key_PageDown) { nudge(-10); e.accepted = true }
    }
}
