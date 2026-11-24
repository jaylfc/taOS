import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Style 1.0

// A few exclusive choices in one pill. options: [{value, label}]; `picked(value)` when the person chooses.
Rectangle {
    id: seg
    property var options: []
    property string current: ""
    property string label: ""
    signal picked(string value)
    implicitHeight: 30
    implicitWidth: rowItem.implicitWidth + 4
    radius: 8
    color: TaosStyle.track
    border.width: 1
    border.color: TaosStyle.hairline
    Accessible.role: Accessible.RadioButton
    Accessible.name: label
    Row {
        id: rowItem
        x: 2; y: 2
        spacing: 0
        Repeater {
            model: seg.options
            delegate: C.AbstractButton {
                id: opt
                required property var modelData
                readonly property bool on: seg.current === modelData.value
                text: modelData.label
                checkable: false
                height: seg.height - 4
                implicitWidth: lbl.implicitWidth + 24
                focusPolicy: Qt.StrongFocus
                Accessible.role: Accessible.RadioButton
                Accessible.name: modelData.label
                Accessible.checked: on
                onClicked: seg.picked(modelData.value)
                Keys.onReturnPressed: seg.picked(modelData.value)
                Keys.onSpacePressed: seg.picked(modelData.value)
                background: Rectangle {
                    radius: 6
                    color: opt.on ? TaosStyle.accent : (opt.hovered ? TaosStyle.raised : "transparent")
                    border.width: opt.visualFocus ? 2 : 0
                    border.color: TaosStyle.focusRing
                    Behavior on color { ColorAnimation { duration: TaosStyle.reduceMotion ? 0 : 150 } }
                }
                contentItem: Txt {
                    id: lbl
                    text: opt.text
                    wrapMode: Text.NoWrap
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    color: opt.on ? TaosStyle.accentText : TaosStyle.text
                    font.weight: Font.Medium
                }
            }
        }
    }
}
