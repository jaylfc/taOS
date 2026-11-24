// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Style 1.0

// A labelled bar. The value is also text (and the accessible description), so
// it never relies on colour alone.
Item {
    id: bar
    property string label: ""
    property real value: 0          // 0..100
    property string valueText: Math.round(Math.max(0, Math.min(100, value))) + "%"
    property int labelWidth: 52
    property color tone: Pal.load(value)
    implicitHeight: 18
    implicitWidth: 200
    Accessible.role: Accessible.ProgressBar
    Accessible.name: label
    Accessible.description: valueText
    Row {
        anchors.fill: parent
        spacing: 8
        Txt {
            visible: bar.label.length > 0
            width: bar.labelWidth
            height: parent.height
            verticalAlignment: Text.AlignVCenter
            text: bar.label
            font.pixelSize: 11
            color: TaosStyle.textTertiary
        }
        Item {
            width: parent.width - (bar.label.length > 0 ? bar.labelWidth + 8 : 0) - 8 - 40
            height: parent.height
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width; height: 6; radius: 3
                color: Pal.track
                Rectangle {
                    height: parent.height; radius: 3
                    width: parent.width * Math.max(0, Math.min(100, bar.value)) / 100
                    color: bar.tone
                    Behavior on width { NumberAnimation { duration: Pal.dur(380); easing.type: Easing.OutCubic } }
                    Behavior on color { ColorAnimation { duration: Pal.dur(380) } }
                }
            }
        }
        Txt {
            width: 40
            height: parent.height
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignRight
            text: bar.valueText
            font.pixelSize: 11
            color: TaosStyle.textSecondary
        }
    }
}
