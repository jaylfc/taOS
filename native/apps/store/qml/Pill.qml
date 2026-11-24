import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Apps 1.0

// A filter pill (a checkable choice). One accent colour marks the selected one.
C.AbstractButton {
    id: pill
    implicitHeight: 26
    implicitWidth: lbl.implicitWidth + 22
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    checkable: true
    Accessible.role: Accessible.RadioButton
    Accessible.name: text
    Accessible.checked: checked
    background: Rectangle {
        radius: 13
        color: pill.checked ? Look.selection : pill.hovered ? Look.hover : Look.field
        border.width: pill.visualFocus ? 2 : 1
        border.color: pill.visualFocus ? Look.focusRing : pill.checked ? Look.tint(Look.accent, 0.5) : Look.hairline
        Behavior on color { ColorAnimation { duration: Look.fast } }
    }
    contentItem: TaosText {
        id: lbl
        text: pill.text
        font.pixelSize: Look.small
        font.weight: pill.checked ? Font.DemiBold : Font.Normal
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
